import pytest
from guardian.config import Guardrail, RolloutConfig, load_config, load_config_from_dict


def test_guardrail_evaluations():
    g_gt = Guardrail(name="err_rate", metric_source="mock", comparison=">", threshold=5.0)
    assert not g_gt.evaluate(4.9)
    assert not g_gt.evaluate(5.0)
    assert g_gt.evaluate(5.1)

    g_gte = Guardrail(name="err_rate", metric_source="mock", comparison=">=", threshold=5.0)
    assert not g_gte.evaluate(4.9)
    assert g_gte.evaluate(5.0)

    g_lt = Guardrail(name="success_rate", metric_source="mock", comparison="<", threshold=95.0)
    assert g_lt.evaluate(94.9)
    assert not g_lt.evaluate(95.0)


def test_config_validation():
    # Valid config
    cfg = RolloutConfig(flag_name="new_checkout", stages=[1, 5, 25, 100], soak_time_seconds=30)
    assert cfg.flag_name == "new_checkout"
    assert cfg.stages == [1, 5, 25, 100]

    # Empty flag
    with pytest.raises(ValueError, match="flag_name must not be empty"):
        RolloutConfig(flag_name="")

    # Out of order stages
    with pytest.raises(ValueError, match="Stages must be in ascending order"):
        RolloutConfig(flag_name="test", stages=[25, 5, 100])

    # Out of range percentage
    with pytest.raises(ValueError, match="Stage percentage must be between 0 and 100"):
        RolloutConfig(flag_name="test", stages=[5, 150])


def test_load_config_from_dict():
    raw = {
        "flag_name": "new_checkout",
        "stages": [5, 50, 100],
        "soak_time_seconds": 15,
        "require_approval_before_final": True,
        "guardrails": [
            {
                "name": "latency_p99",
                "metric_source": "prometheus",
                "comparison": ">",
                "threshold": 250.0,
            }
        ],
    }
    cfg = load_config_from_dict(raw)
    assert cfg.flag_name == "new_checkout"
    assert cfg.stages == [5, 50, 100]
    assert cfg.soak_time_seconds == 15
    assert len(cfg.guardrails) == 1
    assert cfg.guardrails[0].threshold == 250.0


def test_load_config_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("FLAG_NAME", "custom_feature_flag")
    monkeypatch.setenv("FLAGSMITH_ENVIRONMENT_KEY", "env_custom_123")
    monkeypatch.setenv("STAGES", "2, 10, 50, 100")
    monkeypatch.setenv("SOAK_TIME_SECONDS", "45")
    monkeypatch.setenv("GUARDRAIL_QUERY", "sum(rate(errors[1m]))")
    monkeypatch.setenv("GUARDRAIL_THRESHOLD", "3.0")

    non_existent_file = tmp_path / "non_existent.yaml"
    cfg = load_config(non_existent_file)
    assert cfg.flag_name == "custom_feature_flag"
    assert cfg.environment_key == "env_custom_123"
    assert cfg.stages == [2, 10, 50, 100]
    assert cfg.soak_time_seconds == 45
    assert len(cfg.guardrails) == 1
    assert cfg.guardrails[0].threshold == 3.0
    assert cfg.guardrails[0].query == "sum(rate(errors[1m]))"
