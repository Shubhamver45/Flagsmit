"""
FastAPI Server for Guardian.
Provides REST endpoints for status inspection, operator actions, human approval,
and multi-rollout management with SQLite state persistence.
"""

from __future__ import annotations
import hashlib
import hmac
import json
import logging
import os
import secrets
from typing import Any, Dict, Optional
from fastapi import FastAPI, HTTPException, Request, Security, Depends, status, Query
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from guardian.engine import GuardianEngine
from guardian.state_machine import RolloutState
from guardian.status_page import STATUS_PAGE_HTML

logger = logging.getLogger("guardian.api")


class ApproveRequest(BaseModel):
    flag_name: Optional[str] = None
    reason: Optional[str] = "Operator approved promotion"
    approved_by: Optional[str] = "operator"


class RollbackRequest(BaseModel):
    flag_name: Optional[str] = None
    reason: Optional[str] = "Operator initiated manual rollback"


class ResetRequest(BaseModel):
    flag_name: Optional[str] = None


def create_app(engine: GuardianEngine, api_token: Optional[str] = None) -> FastAPI:
    app = FastAPI(
        title="Guardian Service",
        description="Guarded Rollout Engine for Flagsmith",
        version="1.0.0",
    )

    bearer_scheme = HTTPBearer(auto_error=False)

    async def verify_write_token(
        request: Request,
        auth: Optional[HTTPAuthorizationCredentials] = Security(bearer_scheme),
    ) -> bool:
        expected_token = api_token or os.environ.get("GUARDIAN_API_TOKEN") or os.environ.get("GUARDIAN_TOKEN")
        if not expected_token:
            # Open mode: no authentication configured (local dev/demo)
            return True

        # 1. Authorization: Bearer <token>
        if auth and auth.credentials and secrets.compare_digest(auth.credentials, expected_token):
            return True

        # 2. X-API-Token / X-Guardian-Token header
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
        return STATUS_PAGE_HTML

    @app.get("/health")
    def health() -> Dict[str, str]:
        return {"status": "healthy", "service": "guardian"}

    @app.post("/webhooks/flagsmith")
    async def flagsmith_webhook(request: Request) -> Dict[str, Any]:
        """
        Receives and processes Flagsmith webhook events (FLAG_UPDATED).
        Validates X-Flagsmith-Signature using constant-time comparison when secret is configured.
        """
        raw_body = await request.body()
        sig_header = (
            request.headers.get("X-Flagsmith-Signature")
            or request.headers.get("x-flagsmith-signature")
        )
        secret = os.environ.get("FLAGSMITH_WEBHOOK_SECRET") or getattr(engine.config, "webhook_secret", None)

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

        try:
            payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid JSON payload: {e}",
            )

        return engine.handle_flagsmith_webhook(payload)

    @app.get("/status")
    def get_status(
        flag_name: Optional[str] = None,
        full: bool = Query(False, description="Return full audit trail if true, last 20 by default"),
    ) -> Dict[str, Any]:
        return engine.get_status(flag_name=flag_name, full=full)

    @app.get("/rollouts/{flag_name}/status")
    def get_rollout_status(
        flag_name: str,
        full: bool = Query(False, description="Return full audit trail if true, last 20 by default"),
    ) -> Dict[str, Any]:
        return engine.get_status(flag_name=flag_name, full=full)

    @app.post("/approve", dependencies=[Depends(verify_write_token)])
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

    @app.post("/rollback", dependencies=[Depends(verify_write_token)])
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

    @app.post("/reset", dependencies=[Depends(verify_write_token)])
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

    @app.post("/tick", dependencies=[Depends(verify_write_token)])
    def trigger_tick(flag: Optional[str] = Query(None, alias="flag_name")) -> Dict[str, Any]:
        entry = engine.tick(flag_name=flag)
        return {
            "status": "ok",
            "current_state": engine.current_state,
            "current_percentage": engine.current_percentage,
            "audit_entry": entry.to_dict(),
        }

    return app
