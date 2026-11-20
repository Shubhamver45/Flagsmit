"""
Idempotency and State Persistence Tracker for Guardian Actions.
Prevents duplicate actions, double-firing of webhooks, or redundant rollbacks across ticks and restarts.
"""

from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Set

logger = logging.getLogger(__name__)


class IdempotencyManager:
    def __init__(self, persistence_file: Optional[Path | str] = None):
        self._executed_actions: Set[str] = set()
        self._persistence_file = Path(persistence_file) if persistence_file else None
        self._load()

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
        if key in self._executed_actions:
            logger.info(f"Idempotency: Skipping duplicate action '{key}' (already executed)")
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
        self._executed_actions.add(key)
        self._save()

    def is_executed(
        self,
        flag_name: str,
        decision: str,
        to_state: str,
        stage_index: int,
    ) -> bool:
        key = self._generate_key(flag_name, decision, to_state, stage_index)
        return key in self._executed_actions

    def reset(self) -> None:
        self._executed_actions.clear()
        if self._persistence_file and self._persistence_file.exists():
            try:
                self._persistence_file.unlink()
            except Exception as e:
                logger.warning(f"Could not remove idempotency file: {e}")

    def _load(self) -> None:
        if self._persistence_file and self._persistence_file.exists():
            try:
                with open(self._persistence_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._executed_actions = set(data.get("executed_actions", []))
            except Exception as e:
                logger.warning(f"Could not load idempotency file: {e}")

    def _save(self) -> None:
        if self._persistence_file:
            try:
                self._persistence_file.parent.mkdir(parents=True, exist_ok=True)
                with open(self._persistence_file, "w", encoding="utf-8") as f:
                    json.dump({"executed_actions": list(self._executed_actions)}, f, indent=2)
            except Exception as e:
                logger.warning(f"Could not save idempotency file: {e}")
