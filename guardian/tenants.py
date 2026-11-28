"""
Multi-tenant User Authentication, Workspace Store, and Engine Manager for Flagsmith Guardian.
Enables public SaaS/multi-user usage while keeping each workspace's Flagsmith credentials,
rollout state, push metrics, and tamper-evident audit chain strictly isolated.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from guardian.audit_log import AuditLogger
from guardian.config import Guardrail, RolloutConfig
from guardian.engine import GuardianEngine
from guardian.flagsmith_client import FlagsmithAdminClient
from guardian.metrics.cloudwatch import CloudWatchMetricSource
from guardian.metrics.datadog import DatadogMetricSource
from guardian.metrics.flagsmith_exp import FlagsmithExperimentMetricSource
from guardian.metrics.prometheus import PrometheusMetricSource
from guardian.metrics.push import PushMetricSource
from guardian.metrics.registry import CompositeMetricProvider
from guardian.storage import GuardianStorage

logger = logging.getLogger("guardian.tenants")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
USER_SESSION_TTL_SECONDS = 7 * 24 * 3600  # 7 days
PBKDF2_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    """Hash a password with PBKDF2-HMAC-SHA256 and a random 16-byte salt."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify a password against a stored PBKDF2-HMAC-SHA256 hash in constant time."""
    try:
        algo, iters_s, salt_hex, hash_hex = stored_hash.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        iters = int(iters_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters)
        return secrets.compare_digest(actual, expected)
    except Exception:
        return False


def mask_secret(secret: str) -> str:
    """Return a safely masked representation of an API key."""
    if not secret:
        return ""
    if len(secret) <= 6:
        return "****"
    return f"****{secret[-4:]}"


def _default_guardrails() -> List[Dict[str, Any]]:
    return [
        {
            "name": "checkout_error_rate",
            "metric_source": "push",
            "metric": "checkout",
            "comparison": ">",
            "threshold": 5.0,
            "min_samples": 30,
            "window_seconds": 60,
            "description": "Rollback to 0% if error rate exceeds 5.0%",
        }
    ]


class TenantStore:
    """SQLite persistence for users, user sessions, and isolated tenant workspaces."""

    def __init__(self, db_path: Optional[str | Path] = None, workspaces_dir: Optional[str | Path] = None):
        if db_path is None:
            db_path = os.environ.get("GUARDIAN_DB_PATH", "./data/guardian.db")
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.workspaces_dir = Path(workspaces_dir) if workspaces_dir else (self.db_path.parent / "workspaces")
        self.workspaces_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._local = threading.local()
        self._init_db()

    @property
    def _conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=30.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA busy_timeout=10000;")
            self._local.conn = conn
        return self._local.conn

    def _init_db(self) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL
                );
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    csrf_token TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    created_at REAL NOT NULL
                );
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS workspaces (
                    id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    flagsmith_url TEXT NOT NULL,
                    flagsmith_admin_api_key TEXT NOT NULL,
                    flagsmith_environment_key TEXT NOT NULL,
                    flag_name TEXT NOT NULL,
                    stages_json TEXT NOT NULL,
                    soak_time_seconds INTEGER NOT NULL DEFAULT 30,
                    require_approval_before_final INTEGER NOT NULL DEFAULT 1,
                    guardrails_json TEXT NOT NULL,
                    ingest_api_key TEXT UNIQUE NOT NULL,
                    db_path TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                """
            )
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_ws_owner ON workspaces(owner_user_id);")

    # ── Users & Sessions ────────────────────────────────────────────────────

    def create_user(self, email: str, password: str, name: str = "") -> Dict[str, Any]:
        norm_email = (email or "").strip().lower()
        if not EMAIL_RE.match(norm_email):
            raise ValueError("Please enter a valid email address.")
        if not password or len(password) < 8:
            raise ValueError("Password must be at least 8 characters long.")
        user_id = f"usr_{secrets.token_hex(8)}"
        now = time.time()
        pw_hash = hash_password(password)
        clean_name = (name or "").strip()[:80] or norm_email.split("@")[0]

        with self._lock, self._conn:
            existing = self._conn.execute("SELECT id FROM users WHERE email = ?", (norm_email,)).fetchone()
            if existing:
                raise ValueError("An account with this email already exists.")
            self._conn.execute(
                "INSERT INTO users (id, email, password_hash, name, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, norm_email, pw_hash, clean_name, now),
            )
        return {"id": user_id, "email": norm_email, "name": clean_name, "created_at": now}

    def authenticate_user(self, email: str, password: str) -> Optional[Dict[str, Any]]:
        norm_email = (email or "").strip().lower()
        with self._lock:
            row = self._conn.execute(
                "SELECT id, email, password_hash, name, created_at FROM users WHERE email = ?",
                (norm_email,),
            ).fetchone()
        if not row or not verify_password(password or "", row["password_hash"]):
            return None
        return {"id": row["id"], "email": row["email"], "name": row["name"], "created_at": row["created_at"]}

    def create_session(self, user_id: str) -> Dict[str, Any]:
        session_id = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(24)
        now = time.time()
        expires_at = now + USER_SESSION_TTL_SECONDS
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM user_sessions WHERE expires_at <= ?", (now,))
            self._conn.execute(
                "INSERT INTO user_sessions (session_id, user_id, csrf_token, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
                (session_id, user_id, csrf_token, expires_at, now),
            )
        return {
            "session_id": session_id,
            "csrf_token": csrf_token,
            "expires_at": expires_at,
            "expires_in": USER_SESSION_TTL_SECONDS,
        }

    def get_session_user(self, session_id: str) -> Optional[Dict[str, Any]]:
        if not session_id:
            return None
        now = time.time()
        with self._lock:
            row = self._conn.execute(
                """
                SELECT s.session_id, s.csrf_token, s.expires_at, u.id AS user_id, u.email, u.name
                FROM user_sessions s
                JOIN users u ON u.id = s.user_id
                WHERE s.session_id = ?
                """,
                (session_id,),
            ).fetchone()
            if not row:
                return None
            if now >= float(row["expires_at"]):
                with self._conn:
                    self._conn.execute("DELETE FROM user_sessions WHERE session_id = ?", (session_id,))
                return None
        return {
            "session_id": row["session_id"],
            "csrf_token": row["csrf_token"],
            "user": {"id": row["user_id"], "email": row["email"], "name": row["name"]},
        }

    def delete_session(self, session_id: str) -> None:
        if not session_id:
            return
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM user_sessions WHERE session_id = ?", (session_id,))

    # ── Workspaces ──────────────────────────────────────────────────────────

    def _row_to_public_workspace(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "owner_user_id": row["owner_user_id"],
            "name": row["name"],
            "flagsmith_url": row["flagsmith_url"],
            "flagsmith_admin_api_key_masked": mask_secret(row["flagsmith_admin_api_key"]),
            "flagsmith_environment_key": row["flagsmith_environment_key"],
            "flag_name": row["flag_name"],
            "stages": json.loads(row["stages_json"]),
            "soak_time_seconds": int(row["soak_time_seconds"]),
            "require_approval_before_final": bool(row["require_approval_before_final"]),
            "guardrails": json.loads(row["guardrails_json"]),
            "ingest_api_key": row["ingest_api_key"],
            "is_active": bool(row["is_active"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def create_workspace(
        self,
        owner_user_id: str,
        name: str,
        flagsmith_url: str,
        flagsmith_admin_api_key: str,
        flagsmith_environment_key: str,
        flag_name: str,
        stages: Optional[List[int]] = None,
        soak_time_seconds: int = 30,
        require_approval_before_final: bool = True,
        guardrails: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        clean_name = (name or "").strip()[:80]
        if not clean_name:
            raise ValueError("Workspace name is required.")
        clean_url = (flagsmith_url or "").strip().rstrip("/")
        if not (clean_url.startswith("http://") or clean_url.startswith("https://")):
            raise ValueError("Flagsmith URL must start with http:// or https://")
        clean_flag = (flag_name or "").strip()
        if not clean_flag:
            raise ValueError("Flag name is required.")
        clean_admin_key = (flagsmith_admin_api_key or "").strip()
        clean_env_key = (flagsmith_environment_key or "").strip()
        if not clean_admin_key or not clean_env_key:
            raise ValueError("Flagsmith Admin API Key and Environment Key are required.")

        parsed_stages = [int(s) for s in (stages or [1, 5, 25, 100]) if int(s) >= 0]
        if not parsed_stages:
            parsed_stages = [1, 5, 25, 100]
        soak = max(5, int(soak_time_seconds or 30))
        gr_list = guardrails if guardrails else _default_guardrails()

        ws_id = f"ws_{secrets.token_hex(6)}"
        ingest_key = f"grd_ws_{secrets.token_urlsafe(20)}"
        ws_db_path = str(self.workspaces_dir / f"{ws_id}.db")
        now = time.time()

        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO workspaces (
                    id, owner_user_id, name, flagsmith_url, flagsmith_admin_api_key,
                    flagsmith_environment_key, flag_name, stages_json, soak_time_seconds,
                    require_approval_before_final, guardrails_json, ingest_api_key,
                    db_path, is_active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    ws_id,
                    owner_user_id,
                    clean_name,
                    clean_url,
                    clean_admin_key,
                    clean_env_key,
                    clean_flag,
                    json.dumps(parsed_stages),
                    soak,
                    1 if require_approval_before_final else 0,
                    json.dumps(gr_list),
                    ingest_key,
                    ws_db_path,
                    now,
                    now,
                ),
            )
            row = self._conn.execute("SELECT * FROM workspaces WHERE id = ?", (ws_id,)).fetchone()
        return self._row_to_public_workspace(row)

    def list_workspaces_for_user(self, owner_user_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM workspaces WHERE owner_user_id = ? ORDER BY created_at DESC",
                (owner_user_id,),
            ).fetchall()
        return [self._row_to_public_workspace(r) for r in rows]

    def get_workspace_raw(self, ws_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM workspaces WHERE id = ?", (ws_id,)).fetchone()
        return dict(row) if row else None

    def get_workspace_for_user(self, ws_id: str, owner_user_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM workspaces WHERE id = ? AND owner_user_id = ?",
                (ws_id, owner_user_id),
            ).fetchone()
        return self._row_to_public_workspace(row) if row else None

    def update_workspace(self, ws_id: str, owner_user_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        existing = self.get_workspace_raw(ws_id)
        if not existing or existing["owner_user_id"] != owner_user_id:
            return None

        name = (updates.get("name") if updates.get("name") is not None else existing["name"]).strip()[:80]
        flagsmith_url = (updates.get("flagsmith_url") or existing["flagsmith_url"]).strip().rstrip("/")
        admin_key = (updates.get("flagsmith_admin_api_key") or "").strip() or existing["flagsmith_admin_api_key"]
        env_key = (updates.get("flagsmith_environment_key") or existing["flagsmith_environment_key"]).strip()
        flag_name = (updates.get("flag_name") or existing["flag_name"]).strip()
        stages = updates.get("stages")
        stages_json = json.dumps([int(s) for s in stages]) if stages else existing["stages_json"]
        soak = max(5, int(updates.get("soak_time_seconds") or existing["soak_time_seconds"]))
        req_app = (
            1 if updates["require_approval_before_final"] else 0
            if "require_approval_before_final" in updates and updates["require_approval_before_final"] is not None
            else existing["require_approval_before_final"]
        )
        guardrails = updates.get("guardrails")
        guardrails_json = json.dumps(guardrails) if guardrails else existing["guardrails_json"]
        is_active = (
            1 if updates["is_active"] else 0
            if "is_active" in updates and updates["is_active"] is not None
            else existing["is_active"]
        )
        now = time.time()

        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE workspaces SET
                    name = ?, flagsmith_url = ?, flagsmith_admin_api_key = ?,
                    flagsmith_environment_key = ?, flag_name = ?, stages_json = ?,
                    soak_time_seconds = ?, require_approval_before_final = ?,
                    guardrails_json = ?, is_active = ?, updated_at = ?
                WHERE id = ? AND owner_user_id = ?
                """,
                (
                    name,
                    flagsmith_url,
                    admin_key,
                    env_key,
                    flag_name,
                    stages_json,
                    soak,
                    req_app,
                    guardrails_json,
                    is_active,
                    now,
                    ws_id,
                    owner_user_id,
                ),
            )
            row = self._conn.execute("SELECT * FROM workspaces WHERE id = ?", (ws_id,)).fetchone()
        return self._row_to_public_workspace(row)

    def rotate_ingest_key(self, ws_id: str, owner_user_id: str) -> Optional[str]:
        new_key = f"grd_ws_{secrets.token_urlsafe(20)}"
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE workspaces SET ingest_api_key = ?, updated_at = ? WHERE id = ? AND owner_user_id = ?",
                (new_key, time.time(), ws_id, owner_user_id),
            )
            if cur.rowcount == 0:
                return None
        return new_key

    def delete_workspace(self, ws_id: str, owner_user_id: str) -> bool:
        raw = self.get_workspace_raw(ws_id)
        if not raw or raw["owner_user_id"] != owner_user_id:
            return False
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM workspaces WHERE id = ? AND owner_user_id = ?", (ws_id, owner_user_id))
        try:
            db_file = Path(raw["db_path"])
            for suffix in ("", "-wal", "-shm"):
                p = Path(f"{db_file}{suffix}")
                if p.exists():
                    p.unlink()
        except Exception:
            pass
        return True

    def list_active_workspace_ids(self) -> List[str]:
        with self._lock:
            rows = self._conn.execute("SELECT id FROM workspaces WHERE is_active = 1").fetchall()
        return [r["id"] for r in rows]


class WorkspaceManager:
    """Manages isolated GuardianEngine instances for each tenant workspace."""

    def __init__(self, store: TenantStore):
        self.store = store
        self._engines: Dict[str, GuardianEngine] = {}
        self._lock = threading.RLock()

    def _build_engine_for_raw(self, raw: Dict[str, Any]) -> GuardianEngine:
        stages = json.loads(raw["stages_json"])
        guardrails_data = json.loads(raw["guardrails_json"])
        rules = [
            Guardrail(
                name=str(g.get("name", "guardrail")),
                metric_source=str(g.get("metric_source", "push")),
                metric=str(g.get("metric", "checkout")),
                comparison=str(g.get("comparison", ">")),
                threshold=float(g.get("threshold", 5.0)),
                min_samples=int(g.get("min_samples", 30)),
                window_seconds=int(g.get("window_seconds", 60)),
                description=str(g.get("description", "")),
            )
            for g in guardrails_data
        ]

        cfg = RolloutConfig(
            flag_name=raw["flag_name"],
            environment_key=raw["flagsmith_environment_key"],
            stages=stages,
            soak_time_seconds=int(raw["soak_time_seconds"]),
            require_approval_before_final=bool(raw["require_approval_before_final"]),
            guardrails=rules,
        )

        client = FlagsmithAdminClient(
            api_url=raw["flagsmith_url"],
            admin_api_key=raw["flagsmith_admin_api_key"],
            environment_key=raw["flagsmith_environment_key"],
        )

        provider = CompositeMetricProvider()
        provider.register_source("push", PushMetricSource())
        provider.register_source("prometheus", PrometheusMetricSource())
        provider.register_source("datadog", DatadogMetricSource())
        provider.register_source("cloudwatch", CloudWatchMetricSource())
        provider.register_source(
            "flagsmith_experiment",
            FlagsmithExperimentMetricSource(
                api_url=raw["flagsmith_url"],
                admin_api_key=raw["flagsmith_admin_api_key"],
                environment_key=raw["flagsmith_environment_key"],
            ),
        )

        storage = GuardianStorage(db_path=raw["db_path"])
        audit_logger = AuditLogger(storage=storage)
        return GuardianEngine(
            config=cfg,
            flagsmith_client=client,
            audit_logger=audit_logger,
            metric_provider=provider,
            storage=storage,
        )

    def get_engine(self, ws_id: str) -> Optional[GuardianEngine]:
        with self._lock:
            if ws_id in self._engines:
                return self._engines[ws_id]
            raw = self.store.get_workspace_raw(ws_id)
            if not raw:
                return None
            engine = self._build_engine_for_raw(raw)
            self._engines[ws_id] = engine
            return engine

    def invalidate(self, ws_id: str) -> None:
        with self._lock:
            self._engines.pop(ws_id, None)

    def tick_all_active(self) -> None:
        for ws_id in self.store.list_active_workspace_ids():
            try:
                engine = self.get_engine(ws_id)
                if engine:
                    engine.tick()
            except Exception as exc:
                logger.warning("Workspace %s tick error: %s", ws_id, exc)
