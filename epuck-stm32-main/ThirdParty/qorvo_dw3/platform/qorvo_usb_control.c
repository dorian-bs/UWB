#include "qorvo_control.h"
#include "usbd_cdc_if.h"

/* One USB OUT packet remains owned by the main loop. The endpoint is not
 * rearmed until it is consumed, so host writes are NAKed rather than dropped
 * when an ACK is waiting for the IN endpoint. */
static const uint8_t * volatile packet;
static volatile uint32_t packet_len;
static volatile uint32_t packet_offset;
static volatile uint32_t generation;
static volatile bool connected;
static uint32_t processing_generation;

void qorvo_usb_control_lifecycle(bool active)
{
    /* USB IRQ only: invalidate mailbox ownership before the class rearms its
     * shared buffer. Parser/ACK reset is deferred to the main loop. */
    generation++;
    connected = active;
    packet = NULL;
    packet_len = packet_offset = 0;
}

void qorvo_usb_control_receive(const uint8_t *data, uint32_t len)
{
    packet_offset = 0;
    packet = data;
    packet_len = len;
}

void qorvo_usb_control_process(void)
{
    uint32_t irq_state = __get_PRIMASK();
    __disable_irq();
    uint32_t current = generation;
    if (processing_generation != current) {
        qorvo_control_reset_transport();
        processing_generation = current;
    }
    bool active = connected;
    if (irq_state == 0) __enable_irq();
    if (!active || generation != current) return;
    qorvo_control_process();

    irq_state = __get_PRIMASK();
    __disable_irq();
    /* Parsing is bounded and performs no radio I/O. Keep buffer consumption,
     * offset commit and rearm together so reconfiguration cannot replace the
     * packet between them. All command execution remains outside this lock. */
    if (generation == current && connected && packet != NULL &&
        packet_offset <= packet_len) {
        uint32_t consumed = (uint32_t)qorvo_control_receive(packet + packet_offset,
                                                           packet_len - packet_offset);
        packet_offset += consumed;
        if (packet_offset == packet_len) {
            packet = NULL;
            CDC_ControlReceiveReady_FS();
        }
    }
    if (irq_state == 0) __enable_irq();
    if (generation == current && connected) qorvo_control_process();
}

bool qorvo_control_send(const char *data, size_t len)
{
    uint32_t irq_state = __get_PRIMASK();
    __disable_irq();
    bool sent = connected && generation == processing_generation &&
                CDC_TransmitControl_FS((const uint8_t *)data, (uint16_t)len) == USBD_OK;
    if (irq_state == 0) __enable_irq();
    return sent;
}
