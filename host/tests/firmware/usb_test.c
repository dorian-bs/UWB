#include "qorvo_control.h"
#include "qorvo_platform.h"
#include "usbd_cdc_if.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

static USBD_CDC_HandleTypeDef cdc;
USBD_HandleTypeDef hUsbDeviceFS = {&cdc, USBD_STATE_CONFIGURED};
extern uint8_t UserRxBufferFS[], UserTxBufferFS[];
static uint8_t *tx;
static uint16_t txlen;
static unsigned rearmed, pauses, mask;
uint32_t __get_PRIMASK(void) { return mask; }
void __disable_irq(void) { mask = 1; }
void __enable_irq(void) { mask = 0; }
void USBD_CDC_SetTxBuffer(USBD_HandleTypeDef *dev, uint8_t *buf, uint16_t len)
{ (void)dev; tx = buf; txlen = len; }
void USBD_CDC_SetRxBuffer(USBD_HandleTypeDef *dev, uint8_t *buf)
{ (void)dev; assert(buf == UserRxBufferFS); }
uint8_t USBD_CDC_TransmitPacket(USBD_HandleTypeDef *dev)
{ (void)dev; assert(mask); cdc.TxState = 1; return USBD_OK; }
void USBD_CDC_ReceivePacket(USBD_HandleTypeDef *dev) { (void)dev; rearmed++; }
void qorvo_control_read(qorvo_radio_state_t *state)
{
    state->channel=9; state->preamble=128; state->data_rate=6800; state->tx_power=7;
    state->tx_ant_dly=16380; state->rx_ant_dly=16380;
}
static bool have_saved;
static unsigned saves;
static qorvo_radio_state_t saved_state;
bool qorvo_settings_load(qorvo_radio_state_t *state)
{
    if (!have_saved) return false;
    *state = saved_state;
    return true;
}
bool qorvo_settings_save(const qorvo_radio_state_t *state)
{
    have_saved = true; saves++; saved_state = *state;
    return true;
}
void qorvo_control_pause(void) { pauses++; }
bool qorvo_control_apply(const qorvo_radio_state_t *state) { (void)state; return true; }
bool qorvo_control_resume(void) { return true; }

static void out(const char *data)
{
    uint32_t len = (uint32_t)strlen(data);
    memcpy(UserRxBufferFS, data, len);
    assert(USBD_Interface_fops_FS.Receive(UserRxBufferFS, &len) == USBD_OK);
}
static void complete(void)
{
    cdc.TxState = 0;
    uint32_t len = txlen;
    USBD_Interface_fops_FS.TransmitCplt(tx, &len, 0);
}
static void expect(const char *data)
{
    assert(tx == UserTxBufferFS);
    char text[512];
    assert(txlen < sizeof(text));
    memcpy(text, tx, txlen);
    text[txlen] = 0;
    assert(strstr(text, data));
}
int main(void)
{
    assert(USBD_Interface_fops_FS.Init() == USBD_OK);
    qorvo_control_init(1, false);
    out("UWB 1 PA");
    assert(pauses == 0 && rearmed == 0);
    qorvo_usb_control_process();
    assert(pauses == 0 && rearmed == 1);
    char log[] = "old log\n";
    assert(CDC_Transmit_FS((uint8_t *)log, sizeof(log)-1) == USBD_OK);
    memset(log, 'x', sizeof(log));
    expect("old log\n");
    out("USE\nUWB 2 GET\n");
    assert(pauses == 0);
    qorvo_usb_control_process();
    assert(pauses == 1 && qorvo_control_ack_pending() && rearmed == 1);
    expect("old log\n");
    complete();
    assert(CDC_Transmit_FS((uint8_t *)"log\n", 4) == USBD_BUSY);
    qorvo_usb_control_process();
    expect("\"id\":1,\"ok\":true");
    assert(qorvo_control_ack_pending() && rearmed == 2);
    unsigned saved = txlen;
    char copy[512];
    memcpy(copy, tx, saved);
    for (int i=0; i<5; i++) {
        qorvo_usb_control_process();
        assert(CDC_Transmit_FS((uint8_t *)"log\n", 4) == USBD_BUSY);
        assert(memcmp(copy, tx, saved) == 0 && pauses == 1);
    }
    complete();
    qorvo_usb_control_process();
    expect("\"id\":2,\"ok\":true");
    assert(!qorvo_control_ack_pending());
    assert(CDC_Transmit_FS((uint8_t *)"log\n", 4) == USBD_BUSY);
    complete();
    hUsbDeviceFS.pClassData = NULL;
    out("UWB 3 GET\n");
    qorvo_usb_control_process();
    assert(qorvo_control_ack_pending());
    hUsbDeviceFS.pClassData = &cdc;
    qorvo_usb_control_process();
    expect("\"id\":3");
    assert(mask == 0);
    complete();
    mask = 1;
    assert(CDC_Transmit_FS((uint8_t *)"log\n", 4) == USBD_OK);
    assert(mask == 1);
    puts("native usb tests passed");
    return 0;
}
