#ifndef QORVO_CONTROL_H
#define QORVO_CONTROL_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct {
    uint32_t revision;
    uint32_t period_ms;
    uint32_t channel;
    uint32_t preamble;
    uint32_t data_rate;
    uint32_t tx_power;
    /* Absolute DW register values, each limited to 0..65535. */
    uint32_t tx_ant_dly;
    uint32_t rx_ant_dly;
    bool running;
} qorvo_radio_state_t;

void qorvo_control_init(uint8_t board, bool responder);
/* Main-loop only: discard USB-session input/ACKs, not the actual radio state. */
void qorvo_control_reset_transport(void);
/* Main-loop only. Stops after one complete frame while its ACK is pending. */
size_t qorvo_control_receive(const uint8_t *data, size_t len);
void qorvo_control_process(void);
bool qorvo_control_ack_pending(void);
/* Loss diagnostics. 0 is the normal behaviour (queued logging, cycle-counter scheduling);
 * 1=legacy blocking log writes, 2=SysTick scheduling, 4=drop all text lines, 8=per-exchange
 * narration (A/B tests and bring-up). Resets the diagnostic counters; emit enables one
 * {"type":"diag"} line per second. */
void qorvo_diag_set(uint32_t flags, bool emit);
const qorvo_radio_state_t *qorvo_control_state(void);

/* Fixed platform hooks; no radio or transmit work happens in USB callbacks. */
void qorvo_control_read(qorvo_radio_state_t *state);
void qorvo_control_pause(void);
bool qorvo_control_apply(const qorvo_radio_state_t *requested);
bool qorvo_control_resume(void);
bool qorvo_control_send(const char *data, size_t len);

/* Non-volatile copy of the last applied settings (period, radio, delays). */
bool qorvo_settings_load(qorvo_radio_state_t *state);
bool qorvo_settings_save(const qorvo_radio_state_t *state);

#endif
