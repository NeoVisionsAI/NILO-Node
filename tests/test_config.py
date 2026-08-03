"""Tests for configuration loading."""

from pathlib import Path

import pytest

from nilo_node.config.loader import load_config


def test_load_config_substitutes_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_API_KEY", "secret-value")
    config_file = tmp_path / "nilo-node.yaml"
    config_file.write_text(
        """
node:
  name: test-node
backend:
  api_key: "${TEST_API_KEY}"
storage:
  base_path: /tmp/data
""",
        encoding="utf-8",
    )
    config = load_config(config_file)
    assert config.node.name == "test-node"
    assert config.backend.api_key == "secret-value"


def test_example_config_loads() -> None:
    path = Path(__file__).resolve().parents[1] / "config" / "nilo-node.example.yaml"
    config = load_config(path)
    assert config.monitoring.default_chunk_duration_sec == 300
