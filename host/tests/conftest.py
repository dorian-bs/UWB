from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("UWB_SOURCE", "demo")
    module = importlib.import_module("uwb_lab.app")
    monkeypatch.setattr(module, "runtime", module.Runtime(data_dir=tmp_path))
    with TestClient(module.app) as connection:
        yield connection
