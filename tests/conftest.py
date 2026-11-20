import pytest
from typing import Any, Dict, List
from guardian.flagsmith_client import FlagsmithClientProtocol


class FakeFlagsmithClient:
    """In-memory fake Flagsmith client for unit testing."""

    def __init__(self):
        self.rollout_calls: List[float] = []
        self.rollback_calls: List[str] = []
        self.current_percentage: float = 0.0
        self.is_enabled: bool = False

    def get_feature(self, flag_name: str) -> Dict[str, Any]:
        return {
            "id": 1,
            "name": flag_name,
            "type": "MULTIVARIATE",
            "multivariate_options": [{"id": 10, "default_percentage_allocation": 0.0}],
        }

    def get_environment_feature_state(self, flag_name: str) -> Dict[str, Any]:
        return {
            "id": 100,
            "feature": 1,
            "enabled": self.is_enabled,
            "multivariate_feature_state_values": [
                {
                    "id": 1000,
                    "multivariate_feature_option": 10,
                    "percentage_allocation": self.current_percentage,
                }
            ],
        }

    def set_percentage_rollout(self, flag_name: str, percentage: float) -> Dict[str, Any]:
        self.current_percentage = float(percentage)
        self.is_enabled = bool(percentage > 0)
        self.rollout_calls.append(self.current_percentage)
        return {"status": "success", "percentage": self.current_percentage}

    def rollback_to_zero(self, flag_name: str, reason: str = "") -> Dict[str, Any]:
        self.current_percentage = 0.0
        self.is_enabled = False
        self.rollback_calls.append(reason)
        return {"status": "rolled_back", "percentage": 0.0, "reason": reason}


@pytest.fixture
def fake_flagsmith():
    return FakeFlagsmithClient()
