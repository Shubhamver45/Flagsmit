"""
Idempotency and State Persistence Tracker for Guardian Actions.
Uses SQLite persistence (via GuardianStorage) to prevent duplicate actions,
double-firing of webhooks, or redundant rollbacks across ticks and restarts.
"""

from __future__ import annotations
import logging
from pathlib import Path
from typing import Optional

from guardian.storage import GuardianStorage

logger = logging.getLogger(__name__)


class IdempotencyManager:
    def __init__(
        self,
        storage: Optional[GuardianStorage] = None,
        persistence_file: Optional[Path | str] = None,  # retained for backwards compatibility
    ):
        if storage is not None:
            self.storage = storage
        else:
            self.storage = GuardianStorage(db_path=persistence_file if persistence_file else None)

    def _generate_key(
        self,
        flag_name: str,
        decision: str,
        to_state: str,
        stage_index: int,
    ) -> str:
        return f"{flag_name}:{decision}:{to_state}:{stage_index}"

    def should_execute(
        self,
        flag_name: str,
        decision: str,
        to_state: str,
        stage_index: int,
    ) -> bool:
        """
        Returns True if this exact action has NOT been executed yet.
        HOLD decisions are non-mutating and not deduplicated.
        """
        if decision == "HOLD":
            return True

        key = self._generate_key(flag_name, decision, to_state, stage_index)
        if self.storage.is_action_executed(key):
            logger.info(f"Idempotency: Skipping duplicate action '{key}' (already executed in SQLite)")
            return False

        return True

    def mark_executed(
        self,
        flag_name: str,
        decision: str,
        to_state: str,
        stage_index: int,
    ) -> None:
        if decision == "HOLD":
            return

        key = self._generate_key(flag_name, decision, to_state, stage_index)
        self.storage.mark_action_executed(
            action_key=key,
            flag_name=flag_name,
            decision=decision,
            to_state=to_state,
            stage_index=stage_index,
        )

    def is_executed(
        self,
        flag_name: str,
        decision: str,
        to_state: str,
        stage_index: int,
    ) -> bool:
        key = self._generate_key(flag_name, decision, to_state, stage_index)
        return self.storage.is_action_executed(key)

    def reset(self, flag_name: Optional[str] = None) -> None:
        self.storage.clear_idempotency(flag_name=flag_name)
