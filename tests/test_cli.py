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


def test_cli_config_validate_and_doctor(capsys, monkeypatch):
    with patch("guardian.main.load_env_file"):
        monkeypatch.delenv("GUARDIAN_API_TOKEN", raising=False)
        monkeypatch.delenv("FLAGSMITH_URL", raising=False)
        monkeypatch.delenv("PROMETHEUS_URL", raising=False)
        with patch("sys.argv", ["guardian", "config", "validate", "--config", "guardian.example.yaml"]):
            main()
        out = capsys.readouterr().out
        assert "Config valid" in out
        assert "new_checkout" in out

        with patch("sys.argv", ["guardian", "doctor", "--config", "guardian.example.yaml"]):
            main()
        doc_out = capsys.readouterr().out
        assert "Guardian Environment Doctor" in doc_out
        assert "Python:" in doc_out


def test_cli_rollout_and_audit_subcommands(capsys):
    with patch("guardian.cli.call_api", return_value={"flag_name": "new_checkout", "state": "STAGE_1", "percentage": 1.0}):
        with patch("sys.argv", ["guardian", "rollout", "status"]):
            main()
        assert "STAGE_1" in capsys.readouterr().out

    with patch("guardian.cli.call_api", return_value={"valid": True, "checked_entries": 3, "reason": "Audit hash chain verified"}):
        with patch("sys.argv", ["guardian", "audit", "verify"]):
            main()
        assert "Audit hash chain verified" in capsys.readouterr().out
