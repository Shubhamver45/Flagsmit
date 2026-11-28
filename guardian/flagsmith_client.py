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


class MultivariateRolloutError(ValueError):
    """
    Raised when Guardian is asked to read or step a partial rollout percentage
    directly on a multi-variant Flagsmith experiment feature (>1 variant).
    Variant weights represent experiment traffic split, not overall rollout %.
    """


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
        self._segment_override_ids: Dict[tuple, int] = {}

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
        Retrieves current rollout percentage from Flagsmith.
        - Disabled flag -> 0.0%
        - Single-variant rollout flag (1 multivariate option) -> option's percentage_allocation
        - Multi-variant experiment (>1 multivariate options) -> raises MultivariateRolloutError
          because variant weights are experiment splits, not overall rollout percentage.
        - Standard boolean flag -> 100.0% when enabled
        """
        fs = self.get_environment_feature_state(flag_name)
        if not fs.get("enabled", False):
            return 0.0
        mv_values = fs.get("multivariate_feature_state_values", [])
        if len(mv_values) == 1:
            return float(mv_values[0].get("percentage_allocation", 0.0))
        if len(mv_values) > 1:
            raise MultivariateRolloutError(
                f"Feature '{flag_name}' has {len(mv_values)} multivariate variants; "
                "variant weights cannot be treated as an overall rollout percentage. "
                "Use a dedicated rollout gate flag (boolean or single-variant) for staged percentage rollouts."
            )
        return 100.0

    def _patch_or_publish_feature_state(
        self,
        flag_name: str,
        fs: Dict[str, Any],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Update a FeatureState via v1 PATCH, falling back to v2 Feature Versioning when enabled."""
        fs_id = fs["id"]
        env_id = fs.get("environment") or self.environment_id
        url = f"{self.api_url}/features/featurestates/{fs_id}/"
        resp = self.session.patch(url, json=payload, timeout=self.timeout)
        if resp.ok:
            return resp.json()

        # Flagsmith Cloud environments with v2 feature versioning require creating & publishing a version
        if resp.status_code == 400 and "v2 feature versioning" in (resp.text or "").lower():
            feature_id = fs.get("feature") or self.get_feature(flag_name)["id"]
            fs_update: Dict[str, Any] = {
                "id": fs_id,
                "enabled": payload["enabled"],
            }
            if "feature_state_value" in fs:
                fs_update["feature_state_value"] = fs["feature_state_value"]
            if "multivariate_feature_state_values" in payload:
                fs_update["multivariate_feature_state_values"] = payload["multivariate_feature_state_values"]

            v2_url = f"{self.api_url}/environments/{env_id}/features/{feature_id}/versions/"
            v2_payload = {
                "publish_immediately": True,
                "feature_states_to_update": [fs_update],
            }
            v2_resp = self.session.post(v2_url, json=v2_payload, timeout=self.timeout)
            if v2_resp.ok:
                return v2_resp.json()
            msg = f"Flagsmith v2 version publish {v2_url} failed ({v2_resp.status_code}): {v2_resp.text[:300]}"
            logger.error(msg)
            raise RuntimeError(msg)

        if self.environment_key:
            env_url = f"{self.api_url}/environments/{self.environment_key}/featurestates/{fs_id}/"
            env_resp = self.session.patch(env_url, json=payload, timeout=self.timeout)
            if env_resp.ok:
                return env_resp.json()

        msg = f"Flagsmith PATCH {url} failed ({resp.status_code}): {resp.text[:300]}"
        logger.error(msg)
        raise RuntimeError(msg)

    def set_percentage_rollout(self, flag_name: str, percentage: float) -> Dict[str, Any]:
        """
        Updates the rollout percentage on a feature flag without corrupting multi-variant weights.
        - Single-variant rollout flag (1 option): updates that option's percentage_allocation.
        - Multi-variant experiment (>1 options): never modifies multivariate_feature_state_values;
          only 0% (disable) or 100% (enable) is permitted, partial percentages raise MultivariateRolloutError.
        - Standard boolean flag: enables when percentage > 0, disables when 0.
        """
        fs = self.get_environment_feature_state(flag_name)
        fs_id = fs["id"]
        env_id = fs.get("environment") or self.environment_id
        mv_values = fs.get("multivariate_feature_state_values", [])

        if len(mv_values) == 1:
            mv_id = mv_values[0]["id"]
            mv_opt = mv_values[0]["multivariate_feature_option"]
            mv_opt_id = mv_opt["id"] if isinstance(mv_opt, dict) else mv_opt
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
        elif len(mv_values) > 1:
            if 0.0 < float(percentage) < 100.0:
                raise MultivariateRolloutError(
                    f"Refusing to set {percentage}% directly on multi-variant feature '{flag_name}' "
                    f"({len(mv_values)} variants); doing so would overwrite experiment variant weights. "
                    "Use a dedicated rollout gate flag for staged percentage rollouts."
                )
            payload = {
                "environment": env_id,
                "enabled": bool(float(percentage) > 0.0),
            }
        else:
            # Standard boolean flag: enable when percentage > 0, disable when 0
            payload = {
                "environment": env_id,
                "enabled": bool(percentage > 0),
            }

        return self._patch_or_publish_feature_state(flag_name, fs, payload)

    def rollback_to_zero(self, flag_name: str, reason: str = "") -> Dict[str, Any]:
        """
        Emergency rollback: disables the flag (0% exposure).
        For single-variant rollout flags, also resets percentage_allocation to 0.0.
        For multi-variant experiments (>1 variants), preserves all variant weights untouched.
        """
        fs = self.get_environment_feature_state(flag_name)
        fs_id = fs["id"]
        env_id = fs.get("environment") or self.environment_id
        mv_values = fs.get("multivariate_feature_state_values", [])

        if len(mv_values) == 1:
            mv_id = mv_values[0]["id"]
            mv_opt = mv_values[0]["multivariate_feature_option"]
            mv_opt_id = mv_opt["id"] if isinstance(mv_opt, dict) else mv_opt
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

        return self._patch_or_publish_feature_state(flag_name, fs, payload)

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

    # -------------------------------------------------------------------------
    # Segment Override endpoints (optional pre-percentage segment stage)
    # -------------------------------------------------------------------------
    def get_segment_by_name(self, segment_name: str, project_id: Optional[int] = None) -> Dict[str, Any]:
        """Find a Flagsmith segment by name within the project."""
        pid = project_id or self.project_id
        url = f"{self.api_url}/projects/{pid}/segments/"
        while url:
            resp = self.session.get(url, timeout=self.timeout)
            resp.raise_for_status()
            page = resp.json()
            items = page if isinstance(page, list) else page.get("results", [])
            for seg in items:
                if seg.get("name") == segment_name:
                    return seg
            url = None if isinstance(page, list) else page.get("next")
        raise ValueError(f"Segment '{segment_name}' not found in Flagsmith project {pid}")

    def _find_feature_segment(self, feature_id: int, segment_id: int, env_id: int) -> Optional[Dict[str, Any]]:
        url = f"{self.api_url}/features/feature-segments/"
        resp = self.session.get(
            url,
            params={"environment": env_id, "feature": feature_id},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        items = data if isinstance(data, list) else data.get("results", [])
        for fs_seg in items:
            if fs_seg.get("segment") == segment_id and fs_seg.get("feature") == feature_id:
                return fs_seg
        return None

    def enable_segment_override(
        self,
        flag_name: str,
        segment_name: str,
        enabled: bool = True,
        value: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create or update a Flagsmith segment override on a feature flag."""
        feature = self.get_feature(flag_name)
        feature_id = feature["id"]
        env_id = self.environment_id
        seg = self.get_segment_by_name(segment_name)
        segment_id = seg["id"]

        fs_seg = self._find_feature_segment(feature_id, segment_id, env_id)
        if fs_seg is None:
            seg_resp = self.session.post(
                f"{self.api_url}/features/feature-segments/",
                json={"feature": feature_id, "segment": segment_id, "environment": env_id},
                timeout=self.timeout,
            )
            seg_resp.raise_for_status()
            fs_seg = seg_resp.json()

        fs_seg_id = fs_seg["id"]
        self._segment_override_ids[(flag_name, segment_name)] = fs_seg_id
        # Fetch featurestates for (environment, feature) and match feature_segment in Python
        existing_resp = self.session.get(
            f"{self.api_url}/features/featurestates/",
            params={"environment": env_id, "feature": feature_id},
            timeout=self.timeout,
        )
        existing_resp.raise_for_status()
        existing_data = existing_resp.json()
        all_fs = existing_data if isinstance(existing_data, list) else existing_data.get("results", [])
        existing_items = [item for item in all_fs if item.get("feature_segment") == fs_seg_id]

        payload: Dict[str, Any] = {
            "feature": feature_id,
            "environment": env_id,
            "feature_segment": fs_seg_id,
            "enabled": enabled,
        }
        if value is not None:
            payload["feature_state_value"] = {"type": "unicode", "string_value": str(value)}

        env_fs = self.get_environment_feature_state(flag_name)
        mv_values = env_fs.get("multivariate_feature_state_values", [])
        if len(mv_values) == 1 and not existing_items:
            payload["multivariate_feature_state_values"] = [
                {
                    "multivariate_feature_option": mv_values[0]["multivariate_feature_option"],
                    "percentage_allocation": 100.0 if enabled else 0.0,
                }
            ]
        elif len(mv_values) > 1 and not existing_items:
            payload["multivariate_feature_state_values"] = [
                {
                    "multivariate_feature_option": mv["multivariate_feature_option"],
                    "percentage_allocation": float(mv.get("percentage_allocation", 0.0)),
                }
                for mv in mv_values
            ]

        if existing_items:
            fs_id = existing_items[0]["id"]
            r = self.session.patch(
                f"{self.api_url}/features/featurestates/{fs_id}/",
                json=payload,
                timeout=self.timeout,
            )
        else:
            r = self.session.post(
                f"{self.api_url}/features/featurestates/",
                json=payload,
                timeout=self.timeout,
            )
        if not r.ok:
            raise RuntimeError(f"Flagsmith segment override failed ({r.status_code}): {r.text}")
        return r.json()

    def remove_segment_override(self, flag_name: str, segment_name: str) -> bool:
        """Disable and remove a Flagsmith segment override from a feature flag if present."""
        feature = self.get_feature(flag_name)
        feature_id = feature["id"]
        env_id = self.environment_id
        fs_seg_id = self._segment_override_ids.pop((flag_name, segment_name), None)
        if fs_seg_id is None:
            seg = self.get_segment_by_name(segment_name)
            fs_seg = self._find_feature_segment(feature_id, seg["id"], env_id)
            if fs_seg is None:
                return False
            fs_seg_id = fs_seg["id"]
        # Disable any associated FeatureState first so Flagsmith invalidates environment cache
        fs_resp = self.session.get(
            f"{self.api_url}/features/featurestates/",
            params={"environment": env_id, "feature": feature_id},
            timeout=self.timeout,
        )
        if fs_resp.ok:
            data = fs_resp.json()
            all_fs = data if isinstance(data, list) else data.get("results", [])
            for item in all_fs:
                if item.get("feature_segment") == fs_seg_id:
                    self.session.patch(
                        f"{self.api_url}/features/featurestates/{item['id']}/",
                        json={"feature": feature_id, "environment": env_id, "feature_segment": fs_seg_id, "enabled": False},
                        timeout=self.timeout,
                    )

        del_resp = self.session.delete(
            f"{self.api_url}/features/feature-segments/{fs_seg_id}/",
            timeout=self.timeout,
        )
        del_resp.raise_for_status()
        return True
