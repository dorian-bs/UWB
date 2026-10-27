from __future__ import annotations

import os
import sys

import pytest
import uvicorn

from uwb_lab.__main__ import main


@pytest.fixture
def launch(monkeypatch):
    monkeypatch.setattr(os, "environ", os.environ.copy())
    for name in ("UWB_TCP_HOST", "UWB_SERIAL_PORT", "UWB_RESPONDER_PORT", "UWB_REPLAY_FILE"):
        monkeypatch.delenv(name, raising=False)
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs)))

    def run(*arguments):
        monkeypatch.setattr(sys, "argv", ["uwb-lab", *arguments])
        main()
        return calls

    return run


@pytest.mark.parametrize(
    ("arguments", "required"),
    [
        (["--source", "tcp"], "--tcp-host"),
        (["--source", "serial"], "--serial-port"),
        (["--source", "dual-serial", "--responder-port", "/dev/test-rx"], "--serial-port"),
        (["--source", "dual-serial", "--serial-port", "/dev/test-tx"], "--responder-port"),
        (["--source", "replay"], "--replay-file"),
    ],
)
def test_missing_source_configuration_is_reported_before_launch(launch, capsys, arguments, required):
    with pytest.raises(SystemExit) as error:
        launch(*arguments)
    assert error.value.code == 2
    assert f"{required} is required" in capsys.readouterr().err


def test_demo_does_not_invent_hardware_addresses(launch):
    assert len(launch()) == 1
    assert os.environ["UWB_SOURCE"] == "demo"
    assert os.getenv("UWB_TCP_HOST") is None
    assert os.getenv("UWB_SERIAL_PORT") is None


@pytest.mark.parametrize(
    ("source", "arguments", "expected"),
    [
        ("tcp", ["--tcp-host", "bridge.example"], {"UWB_TCP_HOST": "bridge.example"}),
        ("serial", ["--serial-port", "/dev/test-tx"], {"UWB_SERIAL_PORT": "/dev/test-tx"}),
        (
            "dual-serial",
            ["--serial-port", "/dev/test-tx", "--responder-port", "/dev/test-rx"],
            {"UWB_SERIAL_PORT": "/dev/test-tx", "UWB_RESPONDER_PORT": "/dev/test-rx"},
        ),
        ("replay", ["--replay-file", "sample.ndjson"], {"UWB_REPLAY_FILE": "sample.ndjson"}),
    ],
)
def test_explicit_source_configuration_reaches_the_server(launch, source, arguments, expected):
    calls = launch("--source", source, *arguments)
    assert len(calls) == 1
    assert calls[0][1] == {"host": "127.0.0.1", "port": 8000}
    assert os.environ["UWB_SOURCE"] == source
    assert {name: os.environ[name] for name in expected} == expected


@pytest.mark.parametrize(
    ("source", "settings"),
    [
        ("tcp", {"UWB_TCP_HOST": "bridge.example"}),
        ("serial", {"UWB_SERIAL_PORT": "/dev/test-tx"}),
        ("dual-serial", {"UWB_SERIAL_PORT": "/dev/test-tx", "UWB_RESPONDER_PORT": "/dev/test-rx"}),
    ],
)
def test_hardware_configuration_can_come_from_the_environment(launch, monkeypatch, source, settings):
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    assert len(launch("--source", source)) == 1
    assert {name: os.environ[name] for name in settings} == settings
