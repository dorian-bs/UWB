#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "qorvo_log.h"

static uint32_t mask;
uint32_t __get_PRIMASK(void) { return mask; }
void __disable_irq(void) { mask = 1; }
void __enable_irq(void) { mask = 0; }

static uint8_t cdc[16384], uart[16384];
static size_t cdc_len, uart_len, uart_calls, max_chunk;
static bool cdc_busy, uart_fail;

bool qorvo_log_sink_cdc(const uint8_t *data, uint16_t len)
{
    if (cdc_busy) return false;
    memcpy(cdc + cdc_len, data, len);
    cdc_len += len;
    return true;
}

bool qorvo_log_sink_uart(const uint8_t *data, uint16_t len)
{
    if (uart_fail) return false;
    memcpy(uart + uart_len, data, len);
    uart_len += len;
    uart_calls++;
    if (len > max_chunk) max_chunk = len;
    return true;
}

static void clear(void)
{
    qorvo_log_reset();
    cdc_len = uart_len = uart_calls = max_chunk = 0;
    cdc_busy = uart_fail = false;
}

static uint32_t drain_all(uint32_t now_ms)
{
    for (int guard = 0; qorvo_log_pending() && guard < 100000; guard++) qorvo_log_drain(now_ms++);
    return now_ms;
}

int main(void)
{
    char text[200];
    memset(text, 'x', sizeof(text));

    /* Order and content reach both sinks; the UART is written in small chunks. */
    clear();
    qorvo_log_enqueue((const uint8_t *)"first\r\n", 7, false);
    qorvo_log_enqueue((const uint8_t *)text, sizeof(text), false);
    qorvo_log_enqueue((const uint8_t *)"{\"type\":\"range\"}\r\n", 18, true);
    assert(qorvo_log_pending());
    drain_all(0);
    assert(!qorvo_log_pending());
    assert(cdc_len == 7 + sizeof(text) + 18 && uart_len == cdc_len);
    assert(memcmp(cdc, "first\r\n", 7) == 0 && memcmp(uart, cdc, cdc_len) == 0);
    assert(memcmp(cdc + 7 + sizeof(text), "{\"type\":\"range\"}", 16) == 0);
    assert(max_chunk <= 32 && uart_calls >= (sizeof(text) + 32) / 32);
    assert(mask == 0);

    /* Enqueueing never touches a sink, so an interrupt cannot block on them. */
    clear();
    cdc_busy = uart_fail = true;
    qorvo_log_enqueue((const uint8_t *)"quiet\r\n", 7, false);
    assert(cdc_len == 0 && uart_len == 0 && uart_calls == 0);

    /* Text stops short of the ring so that JSON records still fit. */
    clear();
    int accepted_text = 0;
    for (int i = 0; i < 100; i++) qorvo_log_enqueue((const uint8_t *)text, sizeof(text), false);
    accepted_text = 100 - (int)qorvo_log_stats()->drop;
    assert(qorvo_log_stats()->drop > 0 && qorvo_log_stats()->drop_json == 0);
    assert(accepted_text * (int)(sizeof(text) + 3) <= 4096 - 1024);
    for (int i = 0; i < 4; i++) qorvo_log_enqueue((const uint8_t *)text, sizeof(text), true);
    assert(qorvo_log_stats()->drop_json == 0);
    drain_all(0);
    assert(cdc_len == (size_t)(accepted_text + 4) * sizeof(text));

    /* A ring that wraps many times stays intact on USB; the UART gets whole lines only. */
    clear();
    size_t expected = 0;
    for (int i = 0; i < 400; i++) {
        char line[40];
        int n = snprintf(line, sizeof(line), "line %d\r\n", i);
        qorvo_log_enqueue((const uint8_t *)line, (uint16_t)n, i % 7 == 0);
        expected += (size_t)n;
        if (i % 3 == 0) drain_all(i);
    }
    drain_all(1000);
    assert(qorvo_log_stats()->drop == 0 && cdc_len == expected);
    assert(strstr((char *)cdc, "line 399\r\n") != NULL);
    assert(uart_len > 0 && uart_len <= expected);
    {
        size_t lines = 0;
        for (size_t i = 0; i < uart_len; i++) lines += uart[i] == '\n';
        assert(uart[uart_len - 1] == '\n' && uart[uart_len - 2] == '\r');
        assert(lines + qorvo_log_stats()->uart_skip == 400);
    }

    /* A busy or absent USB host delays a line only briefly, and the UART still gets it. */
    clear();
    cdc_busy = true;
    qorvo_log_enqueue((const uint8_t *)"debug\r\n", 7, false);
    qorvo_log_drain(100);
    assert(qorvo_log_pending() && uart_len == 7);
    qorvo_log_drain(103);
    assert(!qorvo_log_pending() && cdc_len == 0);
    assert(qorvo_log_stats()->cdc_drop == 1 && qorvo_log_stats()->cdc_drop_json == 0);

    /* JSON waits longer, and is counted when USB never frees up. */
    clear();
    cdc_busy = true;
    qorvo_log_enqueue((const uint8_t *)"{\"v\":1}\r\n", 9, true);
    qorvo_log_drain(0);
    qorvo_log_drain(15);
    assert(qorvo_log_pending());
    cdc_busy = false;
    qorvo_log_drain(16);
    assert(!qorvo_log_pending() && cdc_len == 9 && qorvo_log_stats()->cdc_drop_json == 0);

    clear();
    cdc_busy = true;
    qorvo_log_enqueue((const uint8_t *)"{\"v\":1}\r\n", 9, true);
    qorvo_log_drain(0);
    qorvo_log_drain(25);
    assert(!qorvo_log_pending());
    assert(qorvo_log_stats()->cdc_drop == 1 && qorvo_log_stats()->cdc_drop_json == 1);

    /* A slow UART never holds back USB: lines arriving while it is busy skip it, but a JSON record
     * waits for a text line instead of being skipped. */
    clear();
    qorvo_log_enqueue((const uint8_t *)text, sizeof(text), false);
    qorvo_log_enqueue((const uint8_t *)text, sizeof(text), false);
    qorvo_log_enqueue((const uint8_t *)"{\"v\":1}\r\n", 9, true);
    qorvo_log_enqueue((const uint8_t *)"{\"v\":2}\r\n", 9, true);
    qorvo_log_drain(0);
    qorvo_log_drain(1);
    assert(cdc_len == 2 * sizeof(text) && qorvo_log_stats()->uart_skip == 1);
    drain_all(2);
    assert(cdc_len == 2 * sizeof(text) + 18);
    assert(qorvo_log_stats()->uart_skip_json == 0);
    assert(strstr((char *)uart, "{\"v\":1}") != NULL && strstr((char *)uart, "{\"v\":2}") != NULL);

    /* A failing UART abandons that line instead of stalling the queue. */
    clear();
    uart_fail = true;
    qorvo_log_enqueue((const uint8_t *)text, sizeof(text), false);
    qorvo_log_enqueue((const uint8_t *)"next\r\n", 6, false);
    uart_fail = true;
    drain_all(0);
    assert(!qorvo_log_pending() && cdc_len == sizeof(text) + 6);

    puts("native log tests passed");
    return 0;
}
