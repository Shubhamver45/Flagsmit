"""
Flagsmith Admin API Client for Guardian.
Implements the exact flag manipulation endpoints verified in docs/spike.md.
Operates strictly on normal rollout flags (separate from experimentation engine).
"""

from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional, Protocol, Union

logger = logging.getLogger(__name__)

# Default timeouts and retry settings
_DEFAULT_TIMEOUT = 10.0  # seconds


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
        project_id: Optional[int] = None,
        environment_id: Optional[int] = None,
        session: Optional[Any] = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ):
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry

        api_url = api_url.rstrip("/")
        if not api_url.endswith("/api/v1"):
            api_url = f"{api_url}/api/v1"
        self.api_url = api_url
        self.admin_api_key = admin_api_key
        self.environment_key = environment_key
        self._project_id = project_id
        self._environment_id = environment_id
        self.timeout = timeout

        if session is not None:
            self.session = session
        else:
            self.session = requests.Session()
            retry = Retry(
                total=3,
                backoff_factor=0.5,
                status_forcelist=[502, 503, 504],
                allowed_methods=["GET", "HEAD", "OPTIONS"],
            )
            adapter = HTTPAdapter(max_retries=retry)
            self.session.mount("http://", adapter)
            self.session.mount("https://", adapter)

        self.session.headers.update({
            "Authorization": f"Api-Key {self.admin_api_key}",
            "Content-Type": "application/json",
        })

    def get_environment_details(self) -> Dict[str, Any]:
        """Dynamically fetch environment details by environment_key (resolves environment_id and project_id)."""
        url = f"{self.api_url}/environments/{self.environment_key}/"
        resp = self.session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        if self._environment_id is None and "id" in data:
            self._environment_id = data["id"]
        if self._project_id is None and "project" in data:
            self._project_id = data["project"]
        return data

    @property
    def environment_id(self) -> int:
        if self._environment_id is None:
            self.get_environment_details()
        return self._environment_id

    @property
    def project_id(self) -> int:
        if self._project_id is None:
            self.get_environment_details()
        return self._project_id

    def get_feature(self, flag_name: str) -> Dict[str, Any]:
        url = f"{self.api_url}/projects/{self.project_id}/features/"
        resp = self.session.get(url, params={"q": flag_name}, timeout=self.timeout)
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
        resp = self.session.get(url, params={"feature": feature_id}, timeout=self.timeout)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if not results:
            raise ValueError(f"FeatureState for '{flag_name}' not found in environment {self.environment_key}")
        fs = results[0]
        if self._environment_id is None and fs.get("environment"):
            self._environment_id = fs["environment"]
        return fs

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
        env_id = fs.get("environment") or self.environment_id
        mv_values = fs.get("multivariate_feature_state_values", [])

        if mv_values:
            mv_id = mv_values[0]["id"]
            mv_opt_id = mv_values[0]["multivariate_feature_option"]
            payload = {
                "environment": env_id,
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
                "environment": env_id,
                "enabled": bool(percentage > 0),
            }

        url = f"{self.api_url}/features/featurestates/{fs_id}/"
        resp = self.session.patch(url, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def rollback_to_zero(self, flag_name: str, reason: str = "") -> Dict[str, Any]:
        """
        Emergency rollback: sets rollout percentage to 0.0 and disables the flag.
        """
        fs = self.get_environment_feature_state(flag_name)
        fs_id = fs["id"]
        env_id = fs.get("environment") or self.environment_id
        mv_values = fs.get("multivariate_feature_state_values", [])

        if mv_values:
            mv_id = mv_values[0]["id"]
            mv_opt_id = mv_values[0]["multivariate_feature_option"]
            payload = {
                "environment": env_id,
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
                "environment": env_id,
                "enabled": False,
            }

        url = f"{self.api_url}/features/featurestates/{fs_id}/"
        resp = self.session.patch(url, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    # -------------------------------------------------------------------------
    # Discovery endpoints (projects, environments, features)
    # -------------------------------------------------------------------------
    def list_projects(self) -> List[Dict[str, Any]]:
        """List all projects accessible with the current admin API key."""
        resp = self.session.get(f"{self.api_url}/projects/", timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        # Flagsmith returns a list directly (not paginated) for /projects/
        return data if isinstance(data, list) else data.get("results", [])

    def list_environments(self, project_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """List environments for a project (defaults to the current project)."""
        pid = project_id or self.project_id
        resp = self.session.get(
            f"{self.api_url}/environments/",
            params={"project": pid},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("results", data) if isinstance(data, dict) else data

    def list_features(self, project_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """List feature flags for a project (defaults to the current project)."""
        pid = project_id or self.project_id
        results: List[Dict[str, Any]] = []
        url = f"{self.api_url}/projects/{pid}/features/"
        while url:
            resp = self.session.get(url, timeout=self.timeout)
            resp.raise_for_status()
            page = resp.json()
            if isinstance(page, list):
                results.extend(page)
                break
            results.extend(page.get("results", []))
            url = page.get("next")  # follow pagination
        return results

    def rollback_to_zero(self, flag_name: str, reason: str = "") -> Dict[str, Any]:
        """
        Emergency rollback: sets rollout percentage to 0.0 and disables the flag.
        """
        fs = self.get_environment_feature_state(flag_name)
        fs_id = fs["id"]
        env_id = fs.get("environment") or self.environment_id
        mv_values = fs.get("multivariate_feature_state_values", [])

        if mv_values:
            mv_id = mv_values[0]["id"]
            mv_opt_id = mv_values[0]["multivariate_feature_option"]
            payload = {
                "environment": env_id,
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
                "environment": env_id,
                "enabled": False,
            }

        url = f"{self.api_url}/features/featurestates/{fs_id}/"
        resp = self.session.patch(url, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def rollback_to_zero(self, flag_name: str, reason: str = "") -> Dict[str, Any]:
        """
        Emergency rollback: sets rollout percentage to 0.0 and disables the flag.
        """
        fs = self.get_environment_feature_state(flag_name)
        fs_id = fs["id"]
        env_id = fs.get("environment") or self.environment_id
        mv_values = fs.get("multivariate_feature_state_values", [])

        if mv_values:
            mv_id = mv_values[0]["id"]
            mv_opt_id = mv_values[0]["multivariate_feature_option"]
            payload = {
                "environment": env_id,
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
                "environment": env_id,
                "enabled": False,
            }

        url = f"{self.api_url}/features/featurestates/{fs_id}/"
        resp = self.session.patch(url, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    # -------------------------------------------------------------------------
    # Discovery endpoints (projects, environments, features)
    # -------------------------------------------------------------------------
    def list_projects(self) -> List[Dict[str, Any]]:
        """List all projects accessible with the current admin API key."""
        resp = self.session.get(f"{self.api_url}/projects/", timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        # Flagsmith returns a list directly (not paginated) for /projects/
        return data if isinstance(data, list) else data.get("results", [])

    def list_environments(self, project_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """List environments for a project (defaults to the current project)."""
        pid = project_id or self.project_id
        resp = self.session.get(
            f"{self.api_url}/environments/",
            params={"project": pid},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("results", data) if isinstance(data, dict) else data

    def list_features(self, project_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """List feature flags for a project (defaults to the current project)."""
        pid = project_id or self.project_id
        results: List[Dict[str, Any]] = []
        url = f"{self.api_url}/projects/{pid}/features/"
        while url:
            resp = self.session.get(url, timeout=self.timeout)
            resp.raise_for_status()
            page = resp.json()
            if isinstance(page, list):
                results.extend(page)
                break
            results.extend(page.get("results", []))
            url = page.get("next")  # follow pagination
        return results
