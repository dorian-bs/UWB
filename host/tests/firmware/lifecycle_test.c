#include "qorvo_control.h"
#include "qorvo_platform.h"
#include "usbd_cdc_if.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

static size_t receive_interleaved(const uint8_t *data, size_t len);
#define qorvo_control_receive receive_interleaved
#include "qorvo_usb_control.c"
#undef qorvo_control_receive

static USBD_CDC_HandleTypeDef cdc;
USBD_HandleTypeDef hUsbDeviceFS = {&cdc, USBD_STATE_CONFIGURED};
extern uint8_t UserRxBufferFS[], UserTxBufferFS[];
static unsigned mask, sent, pauses, rearmed;
static bool inject, deferred;
static uint8_t *tx;
static uint16_t txlen;

static void out(const char *data)
{
    uint32_t len = (uint32_t)strlen(data);
    memcpy(UserRxBufferFS, data, len);
    USBD_Interface_fops_FS.Receive(UserRxBufferFS, &len);
}
static void reconnect(void)
{
    assert(!mask);
    USBD_Interface_fops_FS.DeInit();
    cdc.TxState = 0;
    USBD_Interface_fops_FS.Init();
}
static void replace_with_shorter_packet(void)
{
    reconnect();
    out("UWB 1 GET\n");
}
uint32_t __get_PRIMASK(void) { return mask; }
void __disable_irq(void) { mask = 1; }
void __enable_irq(void)
{
    mask = 0;
    if (deferred) {
        deferred = false;
        replace_with_shorter_packet();
    }
}
static size_t receive_interleaved(const uint8_t *data, size_t len)
{
    /* An underflowed mailbox length is a failure before any OOB read. */
    assert(len <= APP_RX_DATA_SIZE);
    size_t consumed = qorvo_control_receive(data, len);
    if (inject) {
        inject = false;
        if (mask) deferred = true;
        else replace_with_shorter_packet();
    }
    return consumed;
}
void USBD_CDC_SetTxBuffer(USBD_HandleTypeDef *dev, uint8_t *buf, uint16_t len)
{ (void)dev; tx = buf; txlen = len; }
void USBD_CDC_SetRxBuffer(USBD_HandleTypeDef *dev, uint8_t *buf)
{ (void)dev; assert(buf == UserRxBufferFS); }
uint8_t USBD_CDC_TransmitPacket(USBD_HandleTypeDef *dev)
{ (void)dev; assert(mask); cdc.TxState = 1; sent++; return USBD_OK; }
void USBD_CDC_ReceivePacket(USBD_HandleTypeDef *dev) { (void)dev; rearmed++; }
void qorvo_control_read(qorvo_radio_state_t *state)
{
    state->channel=9; state->preamble=128; state->data_rate=6800; state->tx_power=7;
    state->tx_ant_dly=16380; state->rx_ant_dly=16380;
}
void qorvo_control_pause(void) { pauses++; }
bool qorvo_control_apply(const qorvo_radio_state_t *state) { (void)state; return true; }
bool qorvo_control_resume(void) { return true; }
static void expect(const char *text)
{
    char line[512];
    assert(txlen < sizeof(line));
    memcpy(line, tx, txlen);
    line[txlen] = 0;
    assert(strstr(line, text));
}
int main(void)
{
    USBD_Interface_fops_FS.Init();
    qorvo_control_init(1, false);
    out("UWB 123456 GET\n");
    inject = true;
    qorvo_usb_control_process();
    assert(packet_offset <= packet_len);
    assert(sent == 0);
    qorvo_usb_control_process();
    assert(sent == 1);
    expect("\"id\":1,\"ok\":true");
    cdc.TxState = 0;

    out("UWB 2 PA");
    qorvo_usb_control_process();
    reconnect();
    out("USE\n");
    qorvo_usb_control_process();
    assert(pauses == 0);
    expect("\"ok\":false");
    cdc.TxState = 0;

    /* A complete, but unconsumed, command also belongs to the old session. */
    out("UWB 3 PAUSE\n");
    reconnect();
    unsigned previous = sent;
    qorvo_usb_control_process();
    assert(sent == previous && pauses == 0);

    /* Drop an unsent old ACK, while keeping the radio state it describes. */
    cdc.TxState = 1;
    out("UWB 4 PAUSE\n");
    qorvo_usb_control_process();
    assert(qorvo_control_ack_pending() && pauses == 1);
    reconnect();
    previous = sent;
    qorvo_usb_control_process();
    assert(sent == previous && !qorvo_control_ack_pending());
    assert(!qorvo_control_state()->running);
    out("UWB 5 GET\n");
    qorvo_usb_control_process();
    assert(sent == previous + 1);
    expect("\"id\":5,\"ok\":true");
    expect("\"running\":false");

    /* Deinit alone must not parse or rearm disconnected input. */
    cdc.TxState = 0;
    out("UWB 6 RESUME\n");
    USBD_Interface_fops_FS.DeInit();
    previous = rearmed;
    qorvo_usb_control_process();
    assert(rearmed == previous && !qorvo_control_state()->running);
    assert(mask == 0);
    puts("native lifecycle tests passed");
    return 0;
}
