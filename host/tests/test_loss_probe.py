from uwb_lab.loss_probe import summarize


def test_summarize_reports_yield_and_starved_ticks() -> None:
    arrivals = [(index * 0.18, 100 + index) for index in range(10)]
    tx_diag = {
        "wall_ms": 1800,
        "tick_ms": 1000,
        "polls": 10,
        "ok": 10,
        "compute_failed": 0,
        "rx_to": 0,
        "rx_err": 0,
        "start_failed": 0,
        "cdc_drop_json": 0,
        "isr_ms": 800,
        "uart_isr_ms": 760,
        "exch_max_us": 95000,
        "isr_max_us": 60000,
    }
    result = summarize(100, 1.8, arrivals, tx_diag, {"reply_lat_max_us": 9000, "rx_to": 0, "rx_err": 0})
    assert round(result["yield_pct"]) == 56
    assert result["exchange_success_pct"] == 100
    assert round(result["tick_over_wall"], 2) == 0.56
    assert result["lost_tick_ms_per_poll"] == 80
    assert round(result["tx_uart_in_isr_pct"]) == 42
    assert result["rx_reply_latency_max_ms"] == 9
