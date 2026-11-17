"""Compile the real firmware control implementation without an Arm toolchain."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLATFORM = ROOT / "epuck-stm32-main/ThirdParty/qorvo_dw3/platform"

INCLUDES = [
    f"-I{ROOT / 'host/tests/firmware/stubs'}",
    f"-I{PLATFORM}",
    f"-I{ROOT / 'epuck-stm32-main/USB_DEVICE/App'}",
    f"-I{PLATFORM.parent / 'driver'}",
    f"-I{PLATFORM.parent / 'driver/dw3000'}",
]


@pytest.mark.parametrize("fixture", ["control", "radio", "usb", "lifecycle", "log"])
def test_native_firmware_control(fixture: str) -> None:
    compiler = shutil.which("cc")
    if compiler is None:
        pytest.skip("native C compiler unavailable")
    # Keep artifacts inside the project, not the system temporary directory.
    output = ROOT / f".firmware-control-test-{os.getpid()}"
    sources = {
        "control": [PLATFORM / "qorvo_control.c"],
        "radio": [PLATFORM / "qorvo_radio_control.c"],
        "log": [PLATFORM / "qorvo_log.c"],
        "usb": [
            PLATFORM / "qorvo_control.c",
            PLATFORM / "qorvo_usb_control.c",
            ROOT / "epuck-stm32-main/USB_DEVICE/App/usbd_cdc_if.c",
        ],
        "lifecycle": [
            PLATFORM / "qorvo_control.c",
            ROOT / "epuck-stm32-main/USB_DEVICE/App/usbd_cdc_if.c",
        ],
    }
    try:
        subprocess.run(
            [
                compiler,
                "-std=c11",
                "-Wall",
                "-Wextra",
                "-Werror",
                *INCLUDES,
                str(ROOT / f"host/tests/firmware/{fixture}_test.c"),
                *(str(source) for source in sources[fixture]),
                "-o",
                str(output),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        result = subprocess.run([str(output)], check=True, capture_output=True, text=True)
        assert f"native {fixture} tests passed" in result.stdout
    finally:
        output.unlink(missing_ok=True)


def test_firmware_link_integration_compiles() -> None:
    compiler = shutil.which("cc")
    if compiler is None:
        pytest.skip("native C compiler unavailable")
    subprocess.run(
        [
            compiler,
            "-std=c11",
            "-Wall",
            "-Wextra",
            "-Werror",
            # Three pre-existing helpers are unused when calibration is disabled.
            "-Wno-unused-function",
            "-fsyntax-only",
            *INCLUDES,
            str(PLATFORM / "qorvo_port.c"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
