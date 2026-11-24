"""
Guardian Append-Only Audit Logger.
Every decision (advance, hold, rollback, approve) is recorded with reasons and numerical values.
Persists records in SQLite (via GuardianStorage) and optionally JSONL for backwards compatibility.
"""

from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from guardian.storage import GuardianStorage


@dataclass(frozen=True)
class AuditEntry:
    timestamp: str
    flag_name: str
    from_state: str
    to_state: str
    decision: str  # ADVANCE, HOLD, ROLLBACK, AWAIT_APPROVAL, PROMOTED
    reason: str
    numbers: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AuditLogger:
    def __init__(
        self,
        log_file: Optional[Path | str] = None,
        storage: Optional[GuardianStorage] = None,
    ):
        self._entries: List[AuditEntry] = []
        self._log_file = Path(log_file) if log_file else None
        self.storage = storage

        # If storage is provided, pre-load recent historical entries
        if self.storage:
            recent = self.storage.get_audit_entries(limit=100)
            for r in reversed(recent):
                self._entries.append(AuditEntry(
                    timestamp=r["timestamp"],
                    flag_name=r["flag_name"],
                    from_state=r["from_state"],
                    to_state=r["to_state"],
                    decision=r["decision"],
                    reason=r["reason"],
                    numbers=r["numbers"],
                ))

        if self._log_file:
            self._log_file.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        flag_name: str,
        from_state: str,
        to_state: str,
        decision: str,
        reason: str,
        numbers: Dict[str, Any],
    ) -> AuditEntry:
        timestamp = datetime.now(timezone.utc).isoformat()
        entry = AuditEntry(
            timestamp=timestamp,
            flag_name=flag_name,
            from_state=from_state,
            to_state=to_state,
            decision=decision,
            reason=reason,
            numbers=numbers,
        )
        self._entries.append(entry)

        if self.storage:
            self.storage.record_audit(
                timestamp=timestamp,
                flag_name=flag_name,
                from_state=from_state,
                to_state=to_state,
                decision=decision,
                reason=reason,
                numbers=numbers,
            )

        if self._log_file:
            with open(self._log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry.to_dict()) + "\n")

        return entry

    @property
    def entries(self) -> List[AuditEntry]:
        return list(self._entries)

    def get_latest(self) -> Optional[AuditEntry]:
        return self._entries[-1] if self._entries else None

    def clear(self) -> None:
        self._entries.clear()
