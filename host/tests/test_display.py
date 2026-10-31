"""Run dependency-free browser display logic tests with Node's built-in runner."""

from __future__ import annotations

import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path


class ControlPanelParser(HTMLParser):
    """Collects element ids per <details id=...>; nested <details> add to every open ancestor."""

    def __init__(self) -> None:
        super().__init__()
        self.panels: dict[str, set[str]] = {}
        self.open_panels: set[str] = set()
        self.stack: list[str | None] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        identifier = attributes.get("id")
        if identifier:
            for panel in self.stack:
                if panel:
                    self.panels[panel].add(identifier)
        if tag == "details":
            self.stack.append(identifier)
            if identifier:
                self.panels[identifier] = set()
                if "open" in attributes:
                    self.open_panels.add(identifier)

    def handle_endtag(self, tag: str) -> None:
        if tag == "details" and self.stack:
            self.stack.pop()


def test_experiment_panels_start_collapsed_and_contain_existing_controls() -> None:
    parser = ControlPanelParser()
    page = Path(__file__).parents[1] / "uwb_lab" / "static" / "index.html"
    parser.feed(page.read_text(encoding="utf-8"))
    for panel, controls in {
        "radio-panel": {"radio-form", "radio-fields", "radio-error"},
    }.items():
        assert panel in parser.panels, f"{panel} must be an independent details section"
        assert panel not in parser.open_panels, f"{panel} should start collapsed"
        assert controls <= parser.panels[panel]
    prior_and_batch = {"prior-form", "prior-mode", "prior-error", "prior-revision", "experiment-form"}
    assert prior_and_batch <= parser.panels["experiment-panel"]


def test_display_logic() -> None:
    node = shutil.which("node")
    assert node is not None, "Node.js is required to test the dashboard JavaScript"
    result = subprocess.run(
        [
            node,
            "--test",
            str(Path(__file__).with_name("display.test.cjs")),
            str(Path(__file__).with_name("experiment_analysis.test.cjs")),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
