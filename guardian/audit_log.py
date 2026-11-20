"""
Guardian Append-Only Audit Logger.
Every decision (advance, hold, rollback, approve) is recorded with reasons and numerical values.
"""

from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


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
    def __init__(self, log_file: Optional[Path | str] = None):
        self._entries: List[AuditEntry] = []
        self._log_file = Path(log_file) if log_file else None
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
        entry = AuditEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            flag_name=flag_name,
            from_state=from_state,
            to_state=to_state,
            decision=decision,
            reason=reason,
            numbers=numbers,
        )
        self._entries.append(entry)

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
