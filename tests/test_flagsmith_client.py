import pytest
from unittest.mock import MagicMock
from guardian.flagsmith_client import FlagsmithAdminClient


def test_fake_flagsmith_client(fake_flagsmith):
    assert fake_flagsmith.current_percentage == 0.0
    fake_flagsmith.set_percentage_rollout("new_checkout", 25.0)
    assert fake_flagsmith.current_percentage == 25.0
    assert fake_flagsmith.is_enabled is True

    fake_flagsmith.rollback_to_zero("new_checkout", reason="Breach")
    assert fake_flagsmith.current_percentage == 0.0
    assert fake_flagsmith.is_enabled is False
    assert len(fake_flagsmith.rollback_calls) == 1


def test_flagsmith_admin_client_request_construction():
    mock_session = MagicMock()
    # Mock get feature response
    mock_get_feat_resp = MagicMock()
    mock_get_feat_resp.json.return_value = {
        "results": [{"id": 12, "name": "new_checkout", "type": "MULTIVARIATE"}]
    }
    mock_get_feat_resp.raise_for_status.return_value = None

    # Mock get feature state response
    mock_get_fs_resp = MagicMock()
    mock_get_fs_resp.json.return_value = {
        "results": [
            {
                "id": 44,
                "enabled": True,
                "multivariate_feature_state_values": [
                    {"id": 88, "multivariate_feature_option": 99, "percentage_allocation": 0.0}
                ],
            }
        ]
    }
    mock_get_fs_resp.raise_for_status.return_value = None

    # Mock patch response
    mock_patch_resp = MagicMock()
    mock_patch_resp.json.return_value = {"id": 44, "status": "ok"}
    mock_patch_resp.raise_for_status.return_value = None

    mock_session.get.side_effect = [mock_get_feat_resp, mock_get_fs_resp]
    mock_session.patch.return_value = mock_patch_resp

    client = FlagsmithAdminClient(
        api_url="http://mock-flagsmith:8000/api/v1",
        admin_api_key="mock_key",
        environment_key="mock_env",
        project_id=1,
        environment_id=1,
        session=mock_session,
    )

    client.set_percentage_rollout("new_checkout", 50.0)

    # Verify PATCH was called on the verified endpoint with environment and allocation
    mock_session.patch.assert_called_once_with(
        "http://mock-flagsmith:8000/api/v1/features/featurestates/44/",
        json={
            "environment": 1,
            "enabled": True,
            "multivariate_feature_state_values": [
                {
                    "id": 88,
                    "multivariate_feature_option": 99,
                    "percentage_allocation": 50.0,
                }
            ],
        },
    )
