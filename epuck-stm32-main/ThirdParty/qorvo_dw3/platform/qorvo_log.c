#include "qorvo_log.h"

#include <string.h>

#include "main.h"

#define RING_SIZE       4096U /* power of two */
#define RING_MASK       (RING_SIZE - 1U)
#define ENTRY_HEADER    3U    /* JSON flag, then 16-bit length */
#define DEBUG_RESERVE   1024U /* kept free for JSON range records */
#define UART_CHUNK      32U
#define CDC_WAIT_MS     2U
#define CDC_JSON_WAIT_MS 20U

static uint8_t ring[RING_SIZE];
static volatile uint32_t head; /* advanced by any context with interrupts masked */
static volatile uint32_t tail; /* advanced by the main loop only */
static qorvo_log_stats_t stats;

static uint8_t cur[QORVO_LOG_LINE_MAX];
static uint16_t cur_len;
static uint32_t cur_t0_ms;
static bool cur_json, cur_cdc_done, cur_uart_pending, have_cur;

/* The UART is ~100x slower than USB, so it works on its own copy of a line and only ever takes
 * whole lines; USB is never held back by it. */
static uint8_t ubuf[QORVO_LOG_LINE_MAX];
static uint16_t ulen, uoff;
static bool uart_active, uart_json;

void qorvo_log_reset(void)
{
    head = tail = 0U;
    have_cur = false;
    uart_active = false;
    stats = (qorvo_log_stats_t){0};
}

qorvo_log_stats_t *qorvo_log_stats(void) { return &stats; }

void qorvo_log_stats_reset(void) { stats = (qorvo_log_stats_t){0}; }

bool qorvo_log_pending(void) { return have_cur || uart_active || head != tail; }

void qorvo_log_enqueue(const uint8_t *text, uint16_t len, bool json)
{
    uint32_t irq_state = __get_PRIMASK();
    uint32_t need;
    uint32_t pos;
    uint32_t i;

    if (len > QORVO_LOG_LINE_MAX) len = QORVO_LOG_LINE_MAX;
    need = (uint32_t)len + ENTRY_HEADER;
    __disable_irq();
    if (RING_SIZE - (head - tail) < (json ? need : need + DEBUG_RESERVE)) {
        stats.drop++;
        if (json) stats.drop_json++;
    } else {
        pos = head;
        ring[pos++ & RING_MASK] = json ? 1U : 0U;
        ring[pos++ & RING_MASK] = (uint8_t)(len & 0xFFU);
        ring[pos++ & RING_MASK] = (uint8_t)(len >> 8);
        for (i = 0U; i < len; i++) ring[pos++ & RING_MASK] = text[i];
        head = pos;
    }
    if (irq_state == 0U) __enable_irq();
}

static bool load_next(uint32_t now_ms)
{
    uint32_t pos = tail;
    uint32_t i;

    if (head == tail) return false;
    cur_json = ring[pos++ & RING_MASK] != 0U;
    cur_len = ring[pos++ & RING_MASK];
    cur_len |= (uint16_t)((uint16_t)ring[pos++ & RING_MASK] << 8);
    for (i = 0U; i < cur_len; i++) cur[i] = ring[pos++ & RING_MASK];
    tail = pos;
    cur_cdc_done = false;
    cur_uart_pending = true;
    cur_t0_ms = now_ms;
    have_cur = true;
    return true;
}

void qorvo_log_drain(uint32_t now_ms)
{
    if (!have_cur) (void)load_next(now_ms);

    if (have_cur) {
        if (!cur_cdc_done) {
            if (qorvo_log_sink_cdc(cur, cur_len)) {
                cur_cdc_done = true;
            } else if (now_ms - cur_t0_ms > (cur_json ? CDC_JSON_WAIT_MS : CDC_WAIT_MS)) {
                /* No host, or the endpoint stayed busy: never hold up the UART for it. */
                cur_cdc_done = true;
                stats.cdc_drop++;
                if (cur_json) stats.cdc_drop_json++;
            }
        }

        if (cur_uart_pending) {
            if (!uart_active) {
                memcpy(ubuf, cur, cur_len);
                ulen = cur_len;
                uoff = 0U;
                uart_json = cur_json;
                uart_active = true;
                cur_uart_pending = false;
            } else if (!(cur_json && !uart_json)) {
                /* UART still busy: it misses this line. A JSON record waits instead when only
                 * text is in the way, so the text cannot cost a sample. */
                stats.uart_skip++;
                if (cur_json) stats.uart_skip_json++;
                cur_uart_pending = false;
            }
        }

        if (cur_cdc_done && !cur_uart_pending) have_cur = false;
    }

    if (uart_active) {
        uint16_t n = (uint16_t)(ulen - uoff);
        if (n > UART_CHUNK) n = UART_CHUNK;
        /* A failed chunk abandons the rest of this line rather than stalling the queue. */
        if (qorvo_log_sink_uart(&ubuf[uoff], n)) uoff = (uint16_t)(uoff + n);
        else uoff = ulen;
        if (uoff >= ulen) uart_active = false;
    }
}
