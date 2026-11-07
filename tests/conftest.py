from __future__ import annotations

from pathlib import Path

import pytest

from astra.config import Config


@pytest.fixture()
def cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("ASTRA_HOME", str(tmp_path / "home" / ".astra"))
    config = Config.load(project)
    config.data["approval"] = "auto"
    config.data["model"] = "mock"
    config.data["max_steps"] = 12
    return config


@pytest.fixture()
def project(cfg: Config) -> Path:
    return cfg.cwd
