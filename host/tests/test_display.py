"""Run dependency-free browser display logic tests with Node's built-in runner."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def test_display_logic() -> None:
    node = shutil.which("node")
    assert node is not None, "Node.js is required to test the dashboard JavaScript"
    result = subprocess.run(
        [node, "--test", str(Path(__file__).with_name("display.test.cjs"))],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
