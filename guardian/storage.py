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
        self._lock = threading.RLock()
        self._local = threading.local()
        self._init_db()

    @property
    def _conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
                timeout=30.0,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA busy_timeout=10000;")
            self._local.conn = conn
        return self._local.conn

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
                    first_timestamp TEXT,
                    last_timestamp TEXT,
                    count INTEGER NOT NULL DEFAULT 1,
                    flag_name TEXT NOT NULL,
                    from_state TEXT NOT NULL,
                    to_state TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    numbers_json TEXT NOT NULL
                );
            """)
            # Schema migration for existing audit_log tables
            try:
                cur = self._conn.execute("PRAGMA table_info(audit_log)")
                cols = [row[1] for row in cur.fetchall()]
                if "first_timestamp" not in cols:
                    self._conn.execute("ALTER TABLE audit_log ADD COLUMN first_timestamp TEXT;")
                if "last_timestamp" not in cols:
                    self._conn.execute("ALTER TABLE audit_log ADD COLUMN last_timestamp TEXT;")
                if "count" not in cols:
                    self._conn.execute("ALTER TABLE audit_log ADD COLUMN count INTEGER NOT NULL DEFAULT 1;")
            except Exception:
                pass
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
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS write_ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    flag_name TEXT NOT NULL,
                    percentage REAL NOT NULL,
                    written_at REAL NOT NULL
                );
            """)
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_write_ledger_flag ON write_ledger(flag_name, written_at);")

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
    # Audit Decisions & Pruning
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
            # Collapse consecutive identical HOLD decisions
            if decision == "HOLD":
                last_cur = self._conn.execute(
                    "SELECT id, decision, from_state, to_state, reason, count, first_timestamp FROM audit_log WHERE flag_name = ? ORDER BY id DESC LIMIT 1",
                    (flag_name,),
                )
                last_row = last_cur.fetchone()
                if (
                    last_row
                    and last_row["decision"] == "HOLD"
                    and last_row["from_state"] == from_state
                    and last_row["to_state"] == to_state
                ):
                    current_count = int(last_row["count"] or 1) + 1
                    first_ts = last_row["first_timestamp"] or timestamp
                    self._conn.execute(
                        "UPDATE audit_log SET count = ?, last_timestamp = ?, reason = ?, numbers_json = ? WHERE id = ?",
                        (current_count, timestamp, reason, numbers_json, last_row["id"]),
                    )
                    return last_row["id"]

            cur = self._conn.execute(
                """
                INSERT INTO audit_log (
                    timestamp, first_timestamp, last_timestamp, count, flag_name, from_state, to_state, decision, reason, numbers_json
                ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
                """,
                (timestamp, timestamp, timestamp, flag_name, from_state, to_state, decision, reason, numbers_json),
            )
            row_id = cur.lastrowid
            # Keep SQLite from growing without bound
            self._prune_audit_log_locked(max_entries=1000)
            return row_id

    def prune_audit_log(self, max_entries: int = 1000) -> None:
        with self._lock, self._conn:
            self._prune_audit_log_locked(max_entries=max_entries)

    def _prune_audit_log_locked(self, max_entries: int = 1000) -> None:
        self._conn.execute(
            "DELETE FROM audit_log WHERE id NOT IN (SELECT id FROM audit_log ORDER BY id DESC LIMIT ?)",
            (max_entries,),
        )

    def get_audit_entries(
        self,
        flag_name: Optional[str] = None,
        limit: Optional[int] = None,
        order: str = "ASC",
    ) -> List[Dict[str, Any]]:
        order_col = "id ASC" if order.upper() == "ASC" else "id DESC"
        with self._lock:
            if flag_name:
                if limit is not None:
                    cur = self._conn.execute(
                        f"SELECT * FROM audit_log WHERE flag_name = ? ORDER BY {order_col} LIMIT ?",
                        (flag_name, limit),
                    )
                else:
                    cur = self._conn.execute(
                        f"SELECT * FROM audit_log WHERE flag_name = ? ORDER BY {order_col}",
                        (flag_name,),
                    )
            else:
                if limit is not None:
                    cur = self._conn.execute(
                        f"SELECT * FROM audit_log ORDER BY {order_col} LIMIT ?",
                        (limit,),
                    )
                else:
                    cur = self._conn.execute(
                        f"SELECT * FROM audit_log ORDER BY {order_col}"
                    )
            rows = cur.fetchall()
            entries = []
            for r in rows:
                numbers = {}
                try:
                    numbers = json.loads(r["numbers_json"])
                except Exception:
                    pass
                first_ts = r["first_timestamp"] if ("first_timestamp" in r.keys() and r["first_timestamp"]) else r["timestamp"]
                last_ts = r["last_timestamp"] if ("last_timestamp" in r.keys() and r["last_timestamp"]) else r["timestamp"]
                count_val = r["count"] if ("count" in r.keys() and r["count"]) else 1
                entries.append({
                    "id": r["id"],
                    "timestamp": r["timestamp"],
                    "first_timestamp": first_ts,
                    "last_timestamp": last_ts,
                    "count": count_val,
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

    # -------------------------------------------------------------------------
    # Write Ledger (Echo Tracker for Webhook & Reconciler Drift Immunity)
    # -------------------------------------------------------------------------
    def record_write(
        self,
        flag_name: str,
        percentage: float,
        written_at: Optional[float] = None,
    ) -> None:
        written_at = written_at if written_at is not None else time.time()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO write_ledger (flag_name, percentage, written_at)
                VALUES (?, ?, ?);
                """,
                (flag_name, float(percentage), float(written_at)),
            )
            # Prune old writes (> 1 hour old) to keep write ledger bounded
            self._conn.execute(
                "DELETE FROM write_ledger WHERE written_at < ?;",
                (written_at - 3600.0,),
            )

    def is_recent_write(
        self,
        flag_name: str,
        percentage: float,
        window_seconds: float = 30.0,
        now: Optional[float] = None,
    ) -> bool:
        now = now if now is not None else time.time()
        cutoff = now - float(window_seconds)
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT percentage, written_at FROM write_ledger
                WHERE flag_name = ? AND written_at >= ?
                ORDER BY written_at DESC;
                """,
                (flag_name, cutoff),
            )
            rows = cur.fetchall()
            for row in rows:
                if abs(float(row["percentage"]) - float(percentage)) <= 0.01:
                    return True
            return False

    def get_write_ledger(
        self,
        flag_name: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            if flag_name:
                cur = self._conn.execute(
                    """
                    SELECT * FROM write_ledger
                    WHERE flag_name = ?
                    ORDER BY written_at DESC LIMIT ?;
                    """,
                    (flag_name, limit),
                )
            else:
                cur = self._conn.execute(
                    """
                    SELECT * FROM write_ledger
                    ORDER BY written_at DESC LIMIT ?;
                    """,
                    (limit,),
                )
            return [dict(row) for row in cur.fetchall()]

    def close(self) -> None:
        with self._lock:
            if hasattr(self._local, "conn") and self._local.conn is not None:
                try:
                    self._local.conn.close()
                except Exception:
                    pass
                self._local.conn = None
