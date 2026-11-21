"""
Flagsmith Admin API Client for Guardian.
Implements the exact flag manipulation endpoints verified in docs/spike.md.
Operates strictly on normal rollout flags (separate from experimentation engine).
"""

from __future__ import annotations
from typing import Any, Dict, Optional, Protocol, Union


class FlagsmithClientProtocol(Protocol):
    def get_feature(self, flag_name: str) -> Dict[str, Any]:
        ...

    def get_environment_feature_state(self, flag_name: str) -> Dict[str, Any]:
        ...

    def get_percentage_rollout(self, flag_name: str) -> float:
        ...

    def set_percentage_rollout(self, flag_name: str, percentage: float) -> Dict[str, Any]:
        ...

    def rollback_to_zero(self, flag_name: str, reason: str = "") -> Dict[str, Any]:
        ...


class FlagsmithAdminClient:
    """
    Real Flagsmith Admin API client using Master API Key.
    Verified in docs/spike.md.
    """

    def __init__(
        self,
        api_url: str,
        admin_api_key: str,
        environment_key: str,
        project_id: int,
        environment_id: int,
        session: Optional[Any] = None,
    ):
        import requests
        api_url = api_url.rstrip("/")
        if not api_url.endswith("/api/v1"):
            api_url = f"{api_url}/api/v1"
        self.api_url = api_url
        self.admin_api_key = admin_api_key
        self.environment_key = environment_key
        self.project_id = project_id
        self.environment_id = environment_id
        self.session = session or requests.Session()
        self.session.headers.update({
            "Authorization": f"Api-Key {self.admin_api_key}",
            "Content-Type": "application/json",
        })

    def get_feature(self, flag_name: str) -> Dict[str, Any]:
        url = f"{self.api_url}/projects/{self.project_id}/features/"
        resp = self.session.get(url, params={"q": flag_name})
        resp.raise_for_status()
        results = resp.json().get("results", [])
        for feat in results:
            if feat["name"] == flag_name:
                return feat
        raise ValueError(f"Feature '{flag_name}' not found in project {self.project_id}")

    def get_environment_feature_state(self, flag_name: str) -> Dict[str, Any]:
        feature = self.get_feature(flag_name)
        feature_id = feature["id"]

        # Fetch environment feature states
        url = f"{self.api_url}/environments/{self.environment_key}/featurestates/"
        resp = self.session.get(url, params={"feature": feature_id})
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if not results:
            raise ValueError(f"FeatureState for '{flag_name}' not found in environment {self.environment_key}")
        return results[0]

    def get_percentage_rollout(self, flag_name: str) -> float:
        """
        Retrieves current multivariate rollout percentage from Flagsmith.
        """
        fs = self.get_environment_feature_state(flag_name)
        if not fs.get("enabled", False):
            return 0.0
        mv_values = fs.get("multivariate_feature_state_values", [])
        if mv_values:
            return float(mv_values[0].get("percentage_allocation", 0.0))
        return 100.0 if fs.get("enabled", False) else 0.0

    def set_percentage_rollout(self, flag_name: str, percentage: float) -> Dict[str, Any]:
        """
        Updates the rollout percentage on a normal feature flag using multivariate weights
        or segment override, verified in docs/spike.md.
        """
        fs = self.get_environment_feature_state(flag_name)
        fs_id = fs["id"]
        mv_values = fs.get("multivariate_feature_state_values", [])

        if mv_values:
            mv_id = mv_values[0]["id"]
            mv_opt_id = mv_values[0]["multivariate_feature_option"]
            payload = {
                "environment": self.environment_id,
                "enabled": True if percentage > 0 else fs.get("enabled", True),
                "multivariate_feature_state_values": [
                    {
                        "id": mv_id,
                        "multivariate_feature_option": mv_opt_id,
                        "percentage_allocation": float(percentage),
                    }
                ],
            }
        else:
            # Standard boolean flag: enable when percentage > 0, disable when 0
            payload = {
                "environment": self.environment_id,
                "enabled": bool(percentage > 0),
            }

        url = f"{self.api_url}/features/featurestates/{fs_id}/"
        resp = self.session.patch(url, json=payload)
        resp.raise_for_status()
        return resp.json()

    def rollback_to_zero(self, flag_name: str, reason: str = "") -> Dict[str, Any]:
        """
        Emergency rollback: sets rollout percentage to 0.0 and disables the flag.
        """
        fs = self.get_environment_feature_state(flag_name)
        fs_id = fs["id"]
        mv_values = fs.get("multivariate_feature_state_values", [])

        if mv_values:
            mv_id = mv_values[0]["id"]
            mv_opt_id = mv_values[0]["multivariate_feature_option"]
            payload = {
                "environment": self.environment_id,
                "enabled": False,
                "multivariate_feature_state_values": [
                    {
                        "id": mv_id,
                        "multivariate_feature_option": mv_opt_id,
                        "percentage_allocation": 0.0,
                    }
                ],
            }
        else:
            payload = {
                "environment": self.environment_id,
                "enabled": False,
            }

        url = f"{self.api_url}/features/featurestates/{fs_id}/"
        resp = self.session.patch(url, json=payload)
        resp.raise_for_status()
        return resp.json()
