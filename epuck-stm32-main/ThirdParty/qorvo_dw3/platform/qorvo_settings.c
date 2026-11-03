#include "qorvo_control.h"
#include "qorvo_platform.h"

/* Last 128 KB sector of the 1 MB flash. The firmware image is far smaller, and
 * a DFU flash of the image leaves this sector (and the saved settings) intact. */
#define SETTINGS_ADDR   0x080E0000UL
#define SETTINGS_SECTOR FLASH_SECTOR_11
#define SETTINGS_MAGIC  0x55574231UL

typedef struct {
    uint32_t magic;
    uint32_t period_ms;
    uint32_t channel;
    uint32_t preamble;
    uint32_t data_rate;
    uint32_t tx_power;
    uint32_t tx_ant_dly;
    uint32_t rx_ant_dly;
    uint32_t check;
} settings_record_t;

static uint32_t checksum(const settings_record_t *r)
{
    const uint32_t *words = (const uint32_t *)r;
    uint32_t sum = 0xA5A5A5A5UL;
    for (size_t i = 0; i < (sizeof(*r) / 4U) - 1U; i++) sum ^= words[i] + (uint32_t)i;
    return sum;
}

bool qorvo_settings_load(qorvo_radio_state_t *state)
{
    const settings_record_t *r = (const settings_record_t *)SETTINGS_ADDR;
    if (r->magic != SETTINGS_MAGIC || r->check != checksum(r)) return false;
    state->period_ms = r->period_ms;
    state->channel = r->channel;
    state->preamble = r->preamble;
    state->data_rate = r->data_rate;
    state->tx_power = r->tx_power;
    state->tx_ant_dly = r->tx_ant_dly;
    state->rx_ant_dly = r->rx_ant_dly;
    return true;
}

bool qorvo_settings_save(const qorvo_radio_state_t *state)
{
    settings_record_t r = {
        .magic = SETTINGS_MAGIC, .period_ms = state->period_ms, .channel = state->channel,
        .preamble = state->preamble, .data_rate = state->data_rate, .tx_power = state->tx_power,
        .tx_ant_dly = state->tx_ant_dly, .rx_ant_dly = state->rx_ant_dly,
    };
    r.check = checksum(&r);

    const settings_record_t *stored = (const settings_record_t *)SETTINGS_ADDR;
    if (stored->magic == SETTINGS_MAGIC && stored->check == r.check) return true;

    FLASH_EraseInitTypeDef erase = {
        .TypeErase = FLASH_TYPEERASE_SECTORS, .Sector = SETTINGS_SECTOR,
        .NbSectors = 1, .VoltageRange = FLASH_VOLTAGE_RANGE_3,
    };
    uint32_t sector_error = 0;
    bool ok = false;
    HAL_FLASH_Unlock();
    if (HAL_FLASHEx_Erase(&erase, &sector_error) == HAL_OK) {
        const uint32_t *words = (const uint32_t *)&r;
        ok = true;
        for (size_t i = 0; ok && i < sizeof(r) / 4U; i++) {
            ok = HAL_FLASH_Program(FLASH_TYPEPROGRAM_WORD, SETTINGS_ADDR + 4U * i, words[i]) == HAL_OK;
        }
    }
    HAL_FLASH_Lock();
    return ok;
}
