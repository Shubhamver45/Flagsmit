"""
FastAPI Server for Guardian.
Provides REST endpoints for status inspection, operator actions, and human approval.
"""

from __future__ import annotations
import os
import secrets
from typing import Any, Dict, Optional
from fastapi import FastAPI, HTTPException, Request, Security, Depends, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from guardian.engine import GuardianEngine
from guardian.state_machine import RolloutState
from guardian.status_page import STATUS_PAGE_HTML


class ApproveRequest(BaseModel):
    reason: Optional[str] = "Operator approved promotion"
    approved_by: Optional[str] = "operator"


class RollbackRequest(BaseModel):
    reason: Optional[str] = "Operator initiated manual rollback"


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

    @app.get("/status")
    def get_status() -> Dict[str, Any]:
        return engine.get_status()

    @app.post("/approve", dependencies=[Depends(verify_write_token)])
    def approve(payload: Optional[ApproveRequest] = None) -> Dict[str, Any]:
        current_state = engine.current_state
        if current_state != RolloutState.AWAITING_APPROVAL.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot approve rollout: Current state is '{current_state}', expected '{RolloutState.AWAITING_APPROVAL.value}'",
            )

        try:
            entry = engine.approve()
            return {
                "status": "success",
                "message": "Promotion approved successfully",
                "current_state": engine.current_state,
                "current_percentage": engine.current_percentage,
                "audit_entry": entry.to_dict(),
            }
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    @app.post("/rollback", dependencies=[Depends(verify_write_token)])
    def manual_rollback(payload: Optional[RollbackRequest] = None) -> Dict[str, Any]:
        reason = payload.reason if payload else "Manual operator rollback"
        entry = engine.manual_rollback(reason=reason)
        return {
            "status": "rolled_back",
            "message": "Rollback executed",
            "current_state": engine.current_state,
            "current_percentage": engine.current_percentage,
            "audit_entry": entry.to_dict(),
        }

    @app.post("/reset", dependencies=[Depends(verify_write_token)])
    def reset_rollout() -> Dict[str, Any]:
        entry = engine.reset()
        return {
            "status": "reset",
            "message": "Rollout reset to PENDING",
            "current_state": engine.current_state,
            "current_percentage": engine.current_percentage,
            "audit_entry": entry.to_dict(),
        }

    @app.post("/tick", dependencies=[Depends(verify_write_token)])
    def trigger_tick() -> Dict[str, Any]:
        entry = engine.tick()
        return {
            "status": "ok",
            "current_state": engine.current_state,
            "current_percentage": engine.current_percentage,
            "audit_entry": entry.to_dict(),
        }

    return app
