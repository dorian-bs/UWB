#ifndef QORVO_LOG_H
#define QORVO_LOG_H

#include <stdbool.h>
#include <stdint.h>

/*
 * Log lines are copied into a ring from any context (including the radio interrupt)
 * and written out by qorvo_log_drain() from the main loop. The interrupt therefore
 * never waits on the 115200-baud UART or on a busy USB endpoint.
 */
#define QORVO_LOG_LINE_MAX 320U

typedef struct {
    volatile uint32_t drop;          /* lines that did not fit in the ring */
    volatile uint32_t drop_json;     /* ... of which JSON records */
    volatile uint32_t cdc_drop;      /* lines given up on after waiting for USB */
    volatile uint32_t cdc_drop_json;
} qorvo_log_stats_t;

/* Sinks, provided by the platform. UART is written in small chunks. */
bool qorvo_log_sink_cdc(const uint8_t *data, uint16_t len);
bool qorvo_log_sink_uart(const uint8_t *data, uint16_t len);

void qorvo_log_reset(void);
/* Any context. JSON records may use the whole ring, text leaves room for them. */
void qorvo_log_enqueue(const uint8_t *text, uint16_t len, bool json);
/* Main loop only; one USB attempt and at most one UART chunk per call. */
void qorvo_log_drain(uint32_t now_ms);
bool qorvo_log_pending(void);
qorvo_log_stats_t *qorvo_log_stats(void);
void qorvo_log_stats_reset(void);

#endif
