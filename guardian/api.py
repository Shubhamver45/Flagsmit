"""
FastAPI Server for Guardian.
Provides REST endpoints for status inspection, operator actions, human approval,
and multi-rollout management with SQLite state persistence.
Hardened with request size limits, rate limiting, constant-time token comparison,
and strict production environment mode enforcement.
"""

from __future__ import annotations
import copy
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException, Request, Security, Depends, status, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from guardian.engine import GuardianEngine
from guardian.state_machine import RolloutState
from guardian.status_page import STATUS_PAGE_HTML

logger = logging.getLogger("guardian.api")


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Rejects request bodies exceeding maximum allowed byte size (413 Payload Too Large)."""
    def __init__(self, app, max_content_length: int = 65536):
        super().__init__(app)
        self.max_content_length = max_content_length

    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > self.max_content_length:
                    return JSONResponse(
                        status_code=413,
                        content={"detail": f"Request entity too large. Maximum allowed size is {self.max_content_length} bytes."},
                    )
            except ValueError:
                pass
        return await call_next(request)


class InMemoryRateLimiter:
    """Thread-safe sliding-window rate limiter per client IP."""
    def __init__(self, max_requests: int = 60, window_seconds: float = 60.0):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests: Dict[str, List[float]] = {}
        self.lock = threading.Lock()

    def check(self, key: str) -> bool:
        now = time.time()
        with self.lock:
            history = self.requests.setdefault(key, [])
            cutoff = now - self.window_seconds
            self.requests[key] = [t for t in history if t > cutoff]
            if len(self.requests[key]) >= self.max_requests:
                return False
            self.requests[key].append(now)
            return True


write_rate_limiter = InMemoryRateLimiter(max_requests=60, window_seconds=60.0)


async def rate_limit_write_endpoints(request: Request):
    """Enforces basic rate limiting on write and webhook endpoints."""
    # Allow test/demo override via GUARDIAN_RATE_LIMIT_PER_MINUTE
    limit = int(os.environ.get("GUARDIAN_RATE_LIMIT_PER_MINUTE", "60"))
    write_rate_limiter.max_requests = limit
    client_ip = request.client.host if request.client else "unknown"
    if not write_rate_limiter.check(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too Many Requests: Rate limit exceeded for write operations",
            headers={"Retry-After": "60"},
        )


class ApproveRequest(BaseModel):
    flag_name: Optional[str] = None
    reason: Optional[str] = "Operator approved promotion"
    approved_by: Optional[str] = "operator"


class RollbackRequest(BaseModel):
    flag_name: Optional[str] = None
    reason: Optional[str] = "Operator initiated manual rollback"


class ResetRequest(BaseModel):
    flag_name: Optional[str] = None


class PauseRequest(BaseModel):
    flag_name: Optional[str] = None


class ResumeRequest(BaseModel):
    flag_name: Optional[str] = None


def create_app(
    engine: GuardianEngine,
    api_token: Optional[str] = None,
    lifespan: Optional[Any] = None,
) -> FastAPI:
    app = FastAPI(
        title="Guardian Service",
        description="Guarded Rollout Engine for Flagsmith",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(RequestSizeLimitMiddleware, max_content_length=65536)

    bearer_scheme = HTTPBearer(auto_error=False)

    async def verify_write_token(
        request: Request,
        auth: Optional[HTTPAuthorizationCredentials] = Security(bearer_scheme),
    ) -> bool:
        # Security: Strictly forbid credentials/tokens in URL query parameters
        for forbidden in ("token", "api_token", "key", "auth"):
            if forbidden in request.query_params:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=f"Unauthorized: Tokens in URL query parameters are forbidden. Pass credentials via 'Authorization: Bearer <token>' or 'X-Guardian-Token' header.",
                    headers={"WWW-Authenticate": "Bearer"},
                )

        expected_token = api_token or os.environ.get("GUARDIAN_API_TOKEN") or os.environ.get("GUARDIAN_TOKEN")
        is_prod = os.environ.get("GUARDIAN_ENV", "").strip().lower() == "production"

        if is_prod and not expected_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unauthorized: GUARDIAN_API_TOKEN must be configured in production mode.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if not expected_token:
            # Open mode: no authentication configured (local dev/demo only)
            return True

        # 1. Authorization: Bearer <token> (constant-time compare)
        if auth and auth.credentials and secrets.compare_digest(auth.credentials, expected_token):
            return True

        # 2. X-API-Token / X-Guardian-Token header (constant-time compare)
        header_token = request.headers.get("X-API-Token") or request.headers.get("X-Guardian-Token")
        if header_token and secrets.compare_digest(header_token, expected_token):
            return True

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: Invalid or missing API token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        # Security: Never inject server-side secrets into served HTML.
        # The dashboard reads the operator token from localStorage only.
        # Set it via the token input field in the UI or via browser console:
        #   localStorage.setItem('guardian_token', '<your-token>')
        return STATUS_PAGE_HTML

    @app.get("/health")
    def health() -> Dict[str, Any]:
        """Fast local process liveness probe."""
        return {"status": "healthy", "service": "guardian"}

    @app.get("/ready")
    def ready() -> JSONResponse:
        """Readiness probe: checks SQLite connectivity and Flagsmith reachability."""
        checks: Dict[str, str] = {}
        db_ok = True

        # 1. SQLite check
        try:
            engine.storage._conn.execute("SELECT 1").fetchone()
            checks["db"] = "ok"
        except Exception as exc:
            checks["db"] = f"error: {exc}"
            db_ok = False

        # 2. Flagsmith reachability (best-effort, 2s timeout)
        try:
            import requests as _req
            flagsmith_url = os.environ.get("FLAGSMITH_URL", "http://localhost:8000")
            r = _req.get(f"{flagsmith_url.rstrip('/')}/api/v1/", timeout=2.0)
            checks["flagsmith"] = "ok" if r.status_code < 500 else f"degraded ({r.status_code})"
        except Exception:
            checks["flagsmith"] = "degraded (unreachable)"

        payload = {
            "status": "ready" if db_ok else "not_ready",
            "service": "guardian",
            **checks,
        }
        return JSONResponse(
            status_code=status.HTTP_200_OK if db_ok else status.HTTP_503_SERVICE_UNAVAILABLE,
            content=payload,
        )

    @app.post(
        "/webhooks/flagsmith",
        dependencies=[Depends(rate_limit_write_endpoints)],
    )
    async def flagsmith_webhook(request: Request) -> Dict[str, Any]:
        """
        Receives and processes Flagsmith webhook events (FLAG_UPDATED).
        Validates X-Flagsmith-Signature using constant-time comparison when secret is configured.
        In GUARDIAN_ENV=production, refuses requests if secret is missing (fails closed).
        """
        raw_body = await request.body()
        sig_header = (
            request.headers.get("X-Flagsmith-Signature")
            or request.headers.get("x-flagsmith-signature")
        )
        secret = os.environ.get("FLAGSMITH_WEBHOOK_SECRET") or getattr(engine.config, "webhook_secret", None)
        is_prod = os.environ.get("GUARDIAN_ENV", "").strip().lower() == "production"

        if is_prod and not secret:
            logger.warning("Rejected webhook in production: FLAGSMITH_WEBHOOK_SECRET is not configured (fail closed).")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Webhook rejected: FLAGSMITH_WEBHOOK_SECRET is not configured in production mode (failing closed)",
            )

        if secret:
            if not sig_header:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Missing X-Flagsmith-Signature header",
                )
            expected_sig = hmac.new(
                secret.encode("utf-8"),
                raw_body,
                hashlib.sha256,
            ).hexdigest()
            if not secrets.compare_digest(expected_sig, sig_header):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid X-Flagsmith-Signature",
                )
            print("✔ webhook signature verified (X-Flagsmith-Signature)")
            logger.info("✔ webhook signature verified (X-Flagsmith-Signature)")
        else:
            logger.info("ℹ [DEMO/OPEN MODE] Webhook processed without signature verification (no secret configured)")

        try:
            payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid JSON payload: {e}",
            )

        return engine.handle_flagsmith_webhook(payload)

    def _is_request_authenticated(request: Request) -> bool:
        expected_token = api_token or os.environ.get("GUARDIAN_API_TOKEN") or os.environ.get("GUARDIAN_TOKEN")
        if not expected_token:
            return False
        auth_header = request.headers.get("Authorization", "")
        if auth_header.lower().startswith("bearer "):
            bearer_val = auth_header[7:].strip()
            if bearer_val and secrets.compare_digest(bearer_val, expected_token):
                return True
        header_token = request.headers.get("X-API-Token") or request.headers.get("X-Guardian-Token")
        if header_token and secrets.compare_digest(header_token, expected_token):
            return True
        return False

    def _sanitize_rollout_public(rd: Dict[str, Any]) -> Dict[str, Any]:
        errored_names: List[str] = []
        for g in rd.get("guardrails", []):
            details = g.get("details")
            if isinstance(details, dict) and "query" in details:
                details.pop("query", None)
            reason_lower = str(g.get("reason") or "").lower()
            if g.get("status") == "ERROR" or "prometheus error:" in reason_lower or "http://" in reason_lower or "https://" in reason_lower:
                g_name = g.get("name", "unknown")
                errored_names.append(g_name)
                g["reason"] = f"Guardrail '{g_name}' could not be evaluated (metrics source unreachable)"
                g["details"] = {}

        def _should_redact_block(text: Optional[str]) -> bool:
            if not text:
                return False
            low = text.lower()
            return bool(errored_names) and (
                "prometheus" in low or "http://" in low or "https://" in low or "error" in low or "failed" in low or "unreachable" in low
            ) or ("http://" in low or "https://" in low or "prometheus error:" in low)

        if _should_redact_block(rd.get("approval_block_reason")):
            g_name = errored_names[0] if errored_names else "unknown"
            rd["approval_block_reason"] = (
                f"Approval blocked: guardrail '{g_name}' could not be evaluated (metrics source unreachable)"
            )

        exp = rd.get("experiment")
        if isinstance(exp, dict) and _should_redact_block(exp.get("block_reason")):
            g_name = errored_names[0] if errored_names else "unknown"
            exp["block_reason"] = (
                f"Approval blocked: guardrail '{g_name}' could not be evaluated (metrics source unreachable)"
            )

        for entry in rd.get("timeline", []):
            r_str = str(entry.get("reason") or "").lower()
            if "prometheus error:" in r_str or "http://" in r_str or "https://" in r_str:
                entry["reason"] = "Guardrail evaluation error (metrics source unreachable)"
            nums = entry.get("numbers")
            if isinstance(nums, dict):
                nums.pop("query", None)
                if "error" in nums:
                    nums["error"] = "metrics source unreachable"
        return rd

    def _sanitize_public_status(data: Dict[str, Any]) -> Dict[str, Any]:
        sanitized = copy.deepcopy(data)
        _sanitize_rollout_public(sanitized)
        rollouts = sanitized.get("rollouts")
        if isinstance(rollouts, dict):
            for r_data in rollouts.values():
                if isinstance(r_data, dict):
                    _sanitize_rollout_public(r_data)
        return sanitized

    @app.get("/status")
    def get_status(
        request: Request,
        flag_name: Optional[str] = None,
        full: bool = Query(False, description="Return full audit trail if true, last 20 by default"),
    ) -> Dict[str, Any]:
        data = engine.get_status(flag_name=flag_name, full=full)
        is_prod = os.environ.get("GUARDIAN_ENV", "").strip().lower() == "production"
        if is_prod and not _is_request_authenticated(request):
            return _sanitize_public_status(data)
        return data

    @app.get("/rollouts/{flag_name}/status")
    def get_rollout_status(
        request: Request,
        flag_name: str,
        full: bool = Query(False, description="Return full audit trail if true, last 20 by default"),
    ) -> Dict[str, Any]:
        data = engine.get_status(flag_name=flag_name, full=full)
        is_prod = os.environ.get("GUARDIAN_ENV", "").strip().lower() == "production"
        if is_prod and not _is_request_authenticated(request):
            return _sanitize_public_status(data)
        return data

    @app.post(
        "/approve",
        dependencies=[Depends(rate_limit_write_endpoints), Depends(verify_write_token)],
    )
    def approve(
        payload: Optional[ApproveRequest] = None,
        flag: Optional[str] = Query(None, alias="flag_name"),
    ) -> Dict[str, Any]:
        target_flag = (payload.flag_name if payload and payload.flag_name else None) or flag or engine.config.flag_name
        sm = engine.state_machines.get(target_flag)
        if not sm:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unknown flag '{target_flag}'",
            )

        current_state = sm.current_state
        if current_state != RolloutState.AWAITING_APPROVAL.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot approve rollout for '{target_flag}': Current state is '{current_state}', expected '{RolloutState.AWAITING_APPROVAL.value}'",
            )

        try:
            entry = engine.approve(flag_name=target_flag)
            return {
                "status": "success",
                "message": f"Promotion approved successfully for '{target_flag}'",
                "flag_name": target_flag,
                "current_state": sm.current_state,
                "current_percentage": sm.current_percentage,
                "audit_entry": entry.to_dict(),
            }
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    @app.post(
        "/rollback",
        dependencies=[Depends(rate_limit_write_endpoints), Depends(verify_write_token)],
    )
    def manual_rollback(
        payload: Optional[RollbackRequest] = None,
        flag: Optional[str] = Query(None, alias="flag_name"),
    ) -> Dict[str, Any]:
        target_flag = (payload.flag_name if payload and payload.flag_name else None) or flag or engine.config.flag_name
        sm = engine.state_machines.get(target_flag)
        if not sm:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unknown flag '{target_flag}'",
            )
        reason = payload.reason if payload and payload.reason else "Manual operator rollback"
        entry = engine.manual_rollback(flag_name=target_flag, reason=reason)
        return {
            "status": "rolled_back",
            "message": f"Rollback executed for '{target_flag}'",
            "flag_name": target_flag,
            "current_state": sm.current_state,
            "current_percentage": sm.current_percentage,
            "audit_entry": entry.to_dict(),
        }

    @app.post(
        "/reset",
        dependencies=[Depends(rate_limit_write_endpoints), Depends(verify_write_token)],
    )
    def reset_rollout(
        payload: Optional[ResetRequest] = None,
        flag: Optional[str] = Query(None, alias="flag_name"),
    ) -> Dict[str, Any]:
        target_flag = (payload.flag_name if payload and payload.flag_name else None) or flag
        try:
            entry = engine.reset(flag_name=target_flag)
            return {
                "status": "reset",
                "message": f"Rollout reset to PENDING{' for ' + target_flag if target_flag else ''}",
                "flag_name": target_flag or engine.config.flag_name,
                "current_state": engine.current_state,
                "current_percentage": engine.current_percentage,
                "audit_entry": entry.to_dict(),
            }
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    @app.post(
        "/tick",
        dependencies=[Depends(rate_limit_write_endpoints), Depends(verify_write_token)],
    )
    def trigger_tick(flag: Optional[str] = Query(None, alias="flag_name")) -> Dict[str, Any]:
        entry = engine.tick(flag_name=flag)
        return {
            "status": "ok",
            "current_state": engine.current_state,
            "current_percentage": engine.current_percentage,
            "audit_entry": entry.to_dict(),
        }

    # -------------------------------------------------------------------------
    # Pause / Resume endpoints
    # -------------------------------------------------------------------------
    @app.post(
        "/pause",
        dependencies=[Depends(rate_limit_write_endpoints), Depends(verify_write_token)],
    )
    def pause_rollout(
        payload: Optional[PauseRequest] = None,
        flag: Optional[str] = Query(None, alias="flag_name"),
    ) -> Dict[str, Any]:
        """Pause a live rollout without rolling back. Resume with POST /resume."""
        target_flag = (payload.flag_name if payload and payload.flag_name else None) or flag or engine.config.flag_name
        try:
            entry = engine.pause(flag_name=target_flag)
            sm = engine.state_machines[target_flag]
            return {
                "status": "paused",
                "message": f"Rollout paused for '{target_flag}'",
                "flag_name": target_flag,
                "current_state": sm.current_state,
                "current_percentage": sm.current_percentage,
                "audit_entry": entry.to_dict(),
            }
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    @app.post(
        "/resume",
        dependencies=[Depends(rate_limit_write_endpoints), Depends(verify_write_token)],
    )
    def resume_rollout(
        payload: Optional[ResumeRequest] = None,
        flag: Optional[str] = Query(None, alias="flag_name"),
    ) -> Dict[str, Any]:
        """Resume a previously paused rollout."""
        target_flag = (payload.flag_name if payload and payload.flag_name else None) or flag or engine.config.flag_name
        try:
            entry = engine.resume(flag_name=target_flag)
            sm = engine.state_machines[target_flag]
            return {
                "status": "resumed",
                "message": f"Rollout resumed for '{target_flag}'",
                "flag_name": target_flag,
                "current_state": sm.current_state,
                "current_percentage": sm.current_percentage,
                "audit_entry": entry.to_dict(),
            }
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # -------------------------------------------------------------------------
    # Incidents endpoint
    # -------------------------------------------------------------------------
    @app.get(
        "/incidents",
        dependencies=[Depends(verify_write_token)],
    )
    def get_incidents(
        flag_name: Optional[str] = None,
        limit: int = Query(20, ge=1, le=200),
        offset: int = Query(0, ge=0),
        include_resolved: bool = True,
    ) -> Dict[str, Any]:
        """Return recent production incidents recorded by Guardian."""
        incidents = engine.storage.get_incidents(
            flag_name=flag_name,
            limit=limit,
            offset=offset,
            include_resolved=include_resolved,
        )
        total = engine.storage.count_incidents(
            flag_name=flag_name,
            include_resolved=include_resolved,
        )
        return {
            "incidents": incidents,
            "count": len(incidents),
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    # -------------------------------------------------------------------------
    # Flagsmith Discovery endpoints (authenticated, secrets redacted)
    # -------------------------------------------------------------------------
    @app.get(
        "/flagsmith/projects",
        dependencies=[Depends(verify_write_token)],
    )
    def list_flagsmith_projects() -> Dict[str, Any]:
        """List Flagsmith projects accessible to the configured admin key."""
        try:
            projects = engine.flagsmith_client.list_projects()
            safe_projects = [
                {"id": p.get("id"), "name": p.get("name"), "organisation": p.get("organisation")}
                for p in projects
            ]
            return {"projects": safe_projects, "count": len(safe_projects)}
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Flagsmith error: {type(e).__name__}")

    @app.get(
        "/flagsmith/environments",
        dependencies=[Depends(verify_write_token)],
    )
    def list_flagsmith_environments(project_id: Optional[int] = None) -> Dict[str, Any]:
        """List Flagsmith environments for a project (API keys redacted)."""
        try:
            envs = engine.flagsmith_client.list_environments(project_id=project_id)
            safe_envs = [
                {"id": env.get("id"), "name": env.get("name"), "project": env.get("project")}
                for env in envs
            ]
            return {"environments": safe_envs, "count": len(safe_envs)}
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Flagsmith error: {type(e).__name__}")

    @app.get(
        "/flagsmith/features",
        dependencies=[Depends(verify_write_token)],
    )
    def list_flagsmith_features(project_id: Optional[int] = None) -> Dict[str, Any]:
        """List Flagsmith feature flags for a project."""
        try:
            features = engine.flagsmith_client.list_features(project_id=project_id)
            safe_features = [
                {
                    "id": f.get("id"),
                    "name": f.get("name"),
                    "type": f.get("type"),
                    "default_enabled": f.get("default_enabled"),
                }
                for f in features
            ]
            return {"features": safe_features, "count": len(safe_features)}
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Flagsmith error: {type(e).__name__}")

    return app
