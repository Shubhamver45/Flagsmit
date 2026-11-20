"""
FastAPI Server for Guardian.
Provides REST endpoints for status inspection, operator actions, and human approval.
"""

from __future__ import annotations
from typing import Any, Dict, Optional
from fastapi import FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from guardian.engine import GuardianEngine
from guardian.state_machine import RolloutState
from guardian.status_page import STATUS_PAGE_HTML


class ApproveRequest(BaseModel):
    reason: Optional[str] = "Operator approved promotion"
    approved_by: Optional[str] = "operator"


class RollbackRequest(BaseModel):
    reason: Optional[str] = "Operator initiated manual rollback"


def create_app(engine: GuardianEngine) -> FastAPI:
    app = FastAPI(
        title="Guardian Service",
        description="Guarded Rollout Engine for Flagsmith",
        version="1.0.0",
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

    @app.post("/approve")
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
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    @app.post("/rollback")
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

    @app.post("/tick")
    def trigger_tick() -> Dict[str, Any]:
        entry = engine.tick()
        return {
            "status": "ok",
            "current_state": engine.current_state,
            "current_percentage": engine.current_percentage,
            "audit_entry": entry.to_dict(),
        }

    return app
