"""
Guardian SQLite Persistence Layer.
Persists rollout state, stage start time, audit decisions, idempotency keys,
and experiment results into a single SQLite database file (default: ./data/guardian.db).
"""

from __future__ import annotations
import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "./data/guardian.db"


class GuardianStorage:
    def __init__(self, db_path: Optional[str | Path] = None):
        if db_path is None:
            db_path = os.environ.get("GUARDIAN_DB_PATH", DEFAULT_DB_PATH)
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            timeout=10.0,
        )
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        with self._lock, self._conn:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS rollouts (
                    flag_name TEXT PRIMARY KEY,
                    current_state TEXT NOT NULL,
                    current_stage_index INTEGER NOT NULL,
                    stage_entered_at REAL,
                    current_percentage REAL NOT NULL,
                    is_approved INTEGER NOT NULL DEFAULT 0,
                    drift_detected INTEGER NOT NULL DEFAULT 0,
                    drift_details TEXT,
                    updated_at REAL NOT NULL
                );
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    flag_name TEXT NOT NULL,
                    from_state TEXT NOT NULL,
                    to_state TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    numbers_json TEXT NOT NULL
                );
            """)
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_flag ON audit_log(flag_name);")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS idempotency (
                    action_key TEXT PRIMARY KEY,
                    flag_name TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    to_state TEXT NOT NULL,
                    stage_index INTEGER NOT NULL,
                    executed_at REAL NOT NULL
                );
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS experiment_results (
                    flag_name TEXT PRIMARY KEY,
                    as_of TEXT,
                    results_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
            """)

    # -------------------------------------------------------------------------
    # Rollout State
    # -------------------------------------------------------------------------
    def save_rollout_state(
        self,
        flag_name: str,
        current_state: str,
        current_stage_index: int,
        stage_entered_at: Optional[float],
        current_percentage: float,
        is_approved: bool = False,
        drift_detected: bool = False,
        drift_details: Optional[Dict[str, Any]] = None,
        now: Optional[float] = None,
    ) -> None:
        now = now if now is not None else time.time()
        drift_str = json.dumps(drift_details, default=str) if drift_details is not None else None
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO rollouts (
                    flag_name, current_state, current_stage_index, stage_entered_at,
                    current_percentage, is_approved, drift_detected, drift_details, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(flag_name) DO UPDATE SET
                    current_state = excluded.current_state,
                    current_stage_index = excluded.current_stage_index,
                    stage_entered_at = excluded.stage_entered_at,
                    current_percentage = excluded.current_percentage,
                    is_approved = excluded.is_approved,
                    drift_detected = excluded.drift_detected,
                    drift_details = excluded.drift_details,
                    updated_at = excluded.updated_at;
                """,
                (
                    flag_name,
                    current_state,
                    current_stage_index,
                    stage_entered_at,
                    float(current_percentage),
                    1 if is_approved else 0,
                    1 if drift_detected else 0,
                    drift_str,
                    now,
                ),
            )

    def load_rollout_state(self, flag_name: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM rollouts WHERE flag_name = ?",
                (flag_name,),
            )
            row = cur.fetchone()
            if not row:
                return None
            drift_details = None
            if row["drift_details"]:
                try:
                    drift_details = json.loads(row["drift_details"])
                except Exception:
                    pass
            return {
                "flag_name": row["flag_name"],
                "current_state": row["current_state"],
                "current_stage_index": row["current_stage_index"],
                "stage_entered_at": row["stage_entered_at"],
                "current_percentage": row["current_percentage"],
                "is_approved": bool(row["is_approved"]),
                "drift_detected": bool(row["drift_detected"]),
                "drift_details": drift_details,
                "updated_at": row["updated_at"],
            }

    def list_rollout_states(self) -> List[Dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM rollouts ORDER BY flag_name ASC")
            results = []
            for row in cur.fetchall():
                drift_details = None
                if row["drift_details"]:
                    try:
                        drift_details = json.loads(row["drift_details"])
                    except Exception:
                        pass
                results.append({
                    "flag_name": row["flag_name"],
                    "current_state": row["current_state"],
                    "current_stage_index": row["current_stage_index"],
                    "stage_entered_at": row["stage_entered_at"],
                    "current_percentage": row["current_percentage"],
                    "is_approved": bool(row["is_approved"]),
                    "drift_detected": bool(row["drift_detected"]),
                    "drift_details": drift_details,
                    "updated_at": row["updated_at"],
                })
            return results

    # -------------------------------------------------------------------------
    # Audit Decisions
    # -------------------------------------------------------------------------
    def record_audit(
        self,
        timestamp: str,
        flag_name: str,
        from_state: str,
        to_state: str,
        decision: str,
        reason: str,
        numbers: Dict[str, Any],
    ) -> int:
        numbers_json = json.dumps(numbers, default=str)
        with self._lock, self._conn:
            cur = self._conn.execute(
                """
                INSERT INTO audit_log (
                    timestamp, flag_name, from_state, to_state, decision, reason, numbers_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (timestamp, flag_name, from_state, to_state, decision, reason, numbers_json),
            )
            return cur.lastrowid

    def get_audit_entries(
        self,
        flag_name: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            if flag_name:
                cur = self._conn.execute(
                    """
                    SELECT * FROM audit_log WHERE flag_name = ?
                    ORDER BY id DESC LIMIT ?
                    """,
                    (flag_name, limit),
                )
            else:
                cur = self._conn.execute(
                    "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?",
                    (limit,),
                )
            rows = cur.fetchall()
            entries = []
            for r in rows:
                numbers = {}
                try:
                    numbers = json.loads(r["numbers_json"])
                except Exception:
                    pass
                entries.append({
                    "id": r["id"],
                    "timestamp": r["timestamp"],
                    "flag_name": r["flag_name"],
                    "from_state": r["from_state"],
                    "to_state": r["to_state"],
                    "decision": r["decision"],
                    "reason": r["reason"],
                    "numbers": numbers,
                })
            return entries

    # -------------------------------------------------------------------------
    # Idempotency
    # -------------------------------------------------------------------------
    def is_action_executed(self, action_key: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "SELECT 1 FROM idempotency WHERE action_key = ?",
                (action_key,),
            )
            return cur.fetchone() is not None

    def mark_action_executed(
        self,
        action_key: str,
        flag_name: str,
        decision: str,
        to_state: str,
        stage_index: int,
        now: Optional[float] = None,
    ) -> None:
        now = now if now is not None else time.time()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO idempotency (
                    action_key, flag_name, decision, to_state, stage_index, executed_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (action_key, flag_name, decision, to_state, stage_index, now),
            )

    def clear_idempotency(self, flag_name: Optional[str] = None) -> None:
        with self._lock, self._conn:
            if flag_name:
                self._conn.execute("DELETE FROM idempotency WHERE flag_name = ?", (flag_name,))
            else:
                self._conn.execute("DELETE FROM idempotency;")

    # -------------------------------------------------------------------------
    # Last Experiment Results
    # -------------------------------------------------------------------------
    def save_experiment_results(
        self,
        flag_name: str,
        results: Dict[str, Any],
        now: Optional[float] = None,
    ) -> None:
        now = now if now is not None else time.time()
        as_of = results.get("as_of")
        results_str = json.dumps(results, default=str)
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO experiment_results (flag_name, as_of, results_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(flag_name) DO UPDATE SET
                    as_of = excluded.as_of,
                    results_json = excluded.results_json,
                    updated_at = excluded.updated_at;
                """,
                (flag_name, as_of, results_str, now),
            )

    def load_experiment_results(self, flag_name: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM experiment_results WHERE flag_name = ?",
                (flag_name,),
            )
            row = cur.fetchone()
            if not row:
                return None
            try:
                data = json.loads(row["results_json"])
                if row["as_of"] and "as_of" not in data:
                    data["as_of"] = row["as_of"]
                return data
            except Exception:
                return None

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass
