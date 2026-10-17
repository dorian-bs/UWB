#ifndef QORVO_RADIO_CONTROL_H
#define QORVO_RADIO_CONTROL_H
#include "qorvo_control.h"

void qorvo_radio_capture_rf(void);
void qorvo_radio_read(qorvo_radio_state_t *state);
bool qorvo_radio_apply(const qorvo_radio_state_t *requested);
#endif
