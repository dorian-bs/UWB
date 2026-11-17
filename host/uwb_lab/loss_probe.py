"""Sample-loss probe: runs a matrix of firmware diagnostic switches over the two USB ports.

Needs firmware with the ``DIAG`` command (``UWB <id> DIAG <flags>``) on both boards and the dashboard
stopped (it holds the ports exclusively). Each scenario sets the poll period and the per-board flags,
records for a fixed time and reports the yield together with the firmware's own counters. Flags 0 is
the shipped behaviour; the other bits switch the old behaviour back on for comparison.

    python -m uwb_lab.loss_probe --tx-port <usb> --rx-port <usb>    (or UWB_SERIAL_PORT / UWB_RESPONDER_PORT)
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# DIAG flag bits, see qorvo_control.h. 0 is the shipped (fixed) behaviour; the bits re-enable
# the old behaviour so the fix can be A/B tested against it.
LEGACY_LOG, LEGACY_TICK, MUTE_TEXT = 1, 2, 4
LEGACY = LEGACY_LOG | LEGACY_TICK

# (name, period_ms, tx_flags, rx_flags): each row isolates one hypothesis.
SCENARIOS: list[tuple[str, int, int, int]] = [
    ("fixed (default)", 100, 0, 0),
    ("legacy log + tick, both", 100, LEGACY, LEGACY),
    ("legacy log only, both", 100, LEGACY_LOG, LEGACY_LOG),
    ("legacy tick only, both", 100, LEGACY_TICK, LEGACY_TICK),
    ("text lines muted, both", 100, MUTE_TEXT, MUTE_TEXT),
    ("fixed TX, legacy log RX", 100, 0, LEGACY_LOG),
    ("legacy log TX, fixed RX", 100, LEGACY_LOG, 0),
    ("fixed (default)", 200, 0, 0),
    ("fixed (default)", 500, 0, 0),
]


@dataclass
class Board:
    role: str
    port: str
    ranges: list[tuple[float, int]] = field(default_factory=list)
    diags: list[dict[str, Any]] = field(default_factory=list)
    replies: dict[int, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        import serial

        self.connection = serial.Serial(self.port, baudrate=115200, timeout=0.1, exclusive=True)
        self.condition = threading.Condition()
        self.next_id = int(time.time()) % 1_000_000 * 100
        self.stop = False
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self) -> None:
        buffer = bytearray()
        while not self.stop:
            buffer.extend(self.connection.read(512))
            while b"\n" in buffer:
                raw, _, rest = buffer.partition(b"\n")
                buffer = bytearray(rest)
                self._handle(raw.decode("utf-8", errors="replace").strip(), time.monotonic())

    def _handle(self, text: str, at: float) -> None:
        if not text.startswith("{"):
            return
        try:
            message = json.loads(text)
        except json.JSONDecodeError:
            return
        if not isinstance(message, dict):
            return
        with self.condition:
            kind = message.get("type")
            if kind == "range":
                self.ranges.append((at, int(message["seq"])))
            elif kind == "diag":
                self.diags.append(message)
            elif kind == "control":
                self.replies[message["id"]] = message
                self.condition.notify_all()

    def command(self, action: str, *args: int | str, timeout: float = 3.0) -> dict[str, Any]:
        self.next_id += 1
        identifier = self.next_id
        wire = " ".join(["UWB", str(identifier), action, *map(str, args)]) + "\n"
        self.connection.write(wire.encode("ascii"))
        deadline = time.monotonic() + timeout
        with self.condition:
            while identifier not in self.replies:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"{self.role}: no reply to {wire.strip()!r}")
                self.condition.wait(remaining)
            reply = self.replies.pop(identifier)
        if not reply.get("ok"):
            raise RuntimeError(f"{self.role}: {wire.strip()!r} rejected: {reply.get('error')}")
        return reply

    def close(self) -> None:
        self.stop = True
        self.thread.join(timeout=1)
        self.connection.close()


def set_period(tx: Board, rx: Board, period_ms: int) -> None:
    states = {board.role: board.command("GET") for board in (tx, rx)}
    if states["tx"]["period_ms"] == period_ms and states["tx"]["running"] and states["rx"]["running"]:
        return
    revision = max(state["revision"] for state in states.values()) + 1
    for board in (tx, rx):
        board.command("PAUSE")
    for board in (tx, rx):
        state = states[board.role]
        board.command(
            "SET",
            revision,
            period_ms,
            state["channel"],
            state["preamble"],
            state["data_rate"],
            state["tx_power"],
        )
    rx.command("RESUME")
    tx.command("RESUME")


def summarize(
    period_ms: int,
    duration_s: float,
    arrivals: list[tuple[float, int]],
    tx_diag: dict[str, Any] | None,
    rx_diag: dict[str, Any] | None,
) -> dict[str, Any]:
    """Yield from host-side arrivals, plus the firmware's own view of the same window."""
    expected = duration_s * 1000 / period_ms
    result: dict[str, Any] = {
        "samples": len(arrivals),
        "yield_pct": 100 * len(arrivals) / expected,
    }
    if len(arrivals) >= 2:
        span = arrivals[-1][1] - arrivals[0][1] + 1
        result["exchange_success_pct"] = 100 * len(arrivals) / span
        gaps = [b[0] - a[0] for a, b in zip(arrivals, arrivals[1:], strict=False)]
        result["median_interval_ms"] = 1000 * statistics.median(gaps)
    if tx_diag:
        wall, tick, polls = tx_diag["wall_ms"], tx_diag["tick_ms"], max(tx_diag["polls"], 1)
        result["tick_over_wall"] = tick / wall if wall else None
        result["lost_tick_ms_per_poll"] = (wall - tick) / polls
        result["tx_isr_duty_pct"] = 100 * tx_diag["isr_ms"] / wall if wall else None
        result["tx_uart_in_isr_pct"] = 100 * tx_diag["uart_isr_ms"] / wall if wall else None
        for key in ("polls", "ok", "compute_failed", "rx_to", "rx_err", "start_failed", "cdc_drop_json"):
            result[f"tx_{key}"] = tx_diag[key]
        result["tx_exch_max_ms"] = tx_diag["exch_max_us"] / 1000
        result["tx_isr_max_ms"] = tx_diag["isr_max_us"] / 1000
        result["tx_log_drop"] = tx_diag.get("log_drop")
        result["tx_log_drop_json"] = tx_diag.get("log_drop_json")
    if rx_diag:
        result["rx_reply_latency_max_ms"] = rx_diag["reply_lat_max_us"] / 1000
        result["rx_rx_to"] = rx_diag["rx_to"]
        result["rx_rx_err"] = rx_diag["rx_err"]
    return result


def run_scenario(
    tx: Board, rx: Board, name: str, period_ms: int, tx_flags: int, rx_flags: int, seconds: float
):
    set_period(tx, rx, period_ms)
    rx.command("DIAG", rx_flags)
    tx.command("DIAG", tx_flags)
    time.sleep(1.0)  # let the first exchanges after the counter reset settle
    with tx.condition:
        start = time.monotonic()
        tx.ranges.clear()
    time.sleep(seconds)
    with tx.condition:
        arrivals = [item for item in tx.ranges if start <= item[0] <= start + seconds]
    time.sleep(1.3)  # one more diagnostic line per board
    with tx.condition, rx.condition:
        tx_diag = tx.diags[-1] if tx.diags else None
        rx_diag = rx.diags[-1] if rx.diags else None
    return {
        "scenario": name,
        "period_ms": period_ms,
        "tx_flags": tx_flags,
        "rx_flags": rx_flags,
        **summarize(period_ms, seconds, arrivals, tx_diag, rx_diag),
    }


def print_table(rows: list[dict[str, Any]]) -> None:
    columns = [
        ("scenario", "{:<30}"),
        ("period_ms", "{:>5}"),
        ("tx_flags", "{:>3}"),
        ("rx_flags", "{:>3}"),
        ("yield_pct", "{:>6.1f}"),
        ("exchange_success_pct", "{:>6.1f}"),
        ("tick_over_wall", "{:>6.2f}"),
        ("lost_tick_ms_per_poll", "{:>7.1f}"),
        ("tx_isr_duty_pct", "{:>6.1f}"),
        ("tx_uart_in_isr_pct", "{:>6.1f}"),
        ("rx_reply_latency_max_ms", "{:>7.1f}"),
        ("tx_rx_to", "{:>5}"),
        ("tx_rx_err", "{:>5}"),
        ("tx_compute_failed", "{:>5}"),
        ("tx_cdc_drop_json", "{:>5}"),
        ("tx_log_drop", "{:>5}"),
        ("tx_log_drop_json", "{:>5}"),
    ]
    short = [
        "scenario",
        "per",
        "txf",
        "rxf",
        "yield%",
        "succ%",
        "tick/w",
        "lost/ms",
        "isr%",
        "uart%",
        "rxlat",
        "to",
        "err",
        "neg",
        "cdc",
        "ldrop",
        "ljson",
    ]
    print(" ".join(f"{name:>{max(len(name), 3)}}" if i else f"{name:<30}" for i, name in enumerate(short)))
    for row in rows:
        cells = []
        for (key, fmt), label in zip(columns, short, strict=True):
            value = row.get(key)
            text = "-" if value is None else fmt.format(value)
            cells.append(text.ljust(30) if key == "scenario" else text.rjust(max(len(label), 3)))
        print(" ".join(cells))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--tx-port", default=os.getenv("UWB_SERIAL_PORT"))
    parser.add_argument("--rx-port", default=os.getenv("UWB_RESPONDER_PORT"))
    parser.add_argument("--seconds", type=float, default=30.0, help="recording time per scenario")
    parser.add_argument("--only", help="comma-separated scenario row numbers (1-based) to run")
    parser.add_argument("--out", type=Path, default=Path("data/loss_probe"))
    args = parser.parse_args()
    if not args.tx_port or not args.rx_port:
        parser.error("both --tx-port/--rx-port (or UWB_SERIAL_PORT/UWB_RESPONDER_PORT) are required")

    chosen = SCENARIOS
    if args.only:
        chosen = [SCENARIOS[int(i) - 1] for i in args.only.split(",")]
    tx, rx = Board("tx", args.tx_port), Board("rx", args.rx_port)
    original = tx.command("GET")
    rows: list[dict[str, Any]] = []
    try:
        for name, period_ms, tx_flags, rx_flags in chosen:
            print(f"-- {name} @ {period_ms} ms (tx flags {tx_flags}, rx flags {rx_flags})", flush=True)
            rows.append(run_scenario(tx, rx, name, period_ms, tx_flags, rx_flags, args.seconds))
    finally:
        for board in (tx, rx):
            try:
                board.command("DIAG", "OFF")
            except (TimeoutError, RuntimeError):
                pass
        try:
            set_period(tx, rx, original["period_ms"])
        except (TimeoutError, RuntimeError) as error:
            print(f"could not restore the original period: {error}")
        tx.close()
        rx.close()
    print_table(rows)
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"{datetime.now(UTC):%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps({"seconds": args.seconds, "rows": rows}, indent=1))
    print(f"saved {path}")


if __name__ == "__main__":
    main()
