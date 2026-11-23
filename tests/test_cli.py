import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
import yaml
from guardian.cli import main


def test_cli_init_non_interactive(tmp_path):
    out_file = tmp_path / "custom_config.yaml"
    env_file = tmp_path / ".env.test"

    args = [
        "init",
        "--flagsmith-url", "https://edge.api.flagsmith.com/api/v1",
        "--admin-api-key", "test_admin_key_123",
        "--environment-key", "test_env_key_456",
        "--flag-name", "my_reusable_flag",
        "--prometheus-url", "http://prom.internal:9090",
        "--stages", "5, 20, 50, 100",
        "--soak", "40",
        "-o", str(out_file),
        "--save-env",
        "--env-file", str(env_file),
        "--skip-verify",
        "--non-interactive",
    ]

    with patch("sys.argv", ["guardian"] + args):
        main()

    assert out_file.exists()
    with open(out_file, "r") as f:
        data = yaml.safe_load(f)

    assert data["flag_name"] == "my_reusable_flag"
    assert data["environment_key"] == "test_env_key_456"
    assert data["stages"] == [5, 20, 50, 100]
    assert data["soak_time_seconds"] == 40
    assert len(data["guardrails"]) >= 1

    assert env_file.exists()
    env_text = env_file.read_text()
    assert "FLAGSMITH_URL=https://edge.api.flagsmith.com/api/v1" in env_text
    assert "FLAGSMITH_ADMIN_API_KEY=test_admin_key_123" in env_text
    assert "FLAGSMITH_ENVIRONMENT_KEY=test_env_key_456" in env_text
    assert "FLAG_NAME=my_reusable_flag" in env_text
