#include "qorvo_control.h"
#include "qorvo_platform.h"

/* Last 128 KB sector of the 1 MB flash. The firmware image is far smaller, and
 * a DFU flash of the image leaves this sector (and the saved settings) intact. */
#define SETTINGS_ADDR   0x080E0000UL
#define SETTINGS_SECTOR FLASH_SECTOR_11
#define SETTINGS_MAGIC  0x55574232UL
#define SETTINGS_MAGIC_V1 0x55574231UL

typedef struct {
    uint32_t magic;
    uint32_t period_ms;
    uint32_t channel;
    uint32_t preamble;
    uint32_t data_rate;
    uint32_t tx_power;
    uint32_t tx_ant_dly;
    uint32_t rx_ant_dly;
    uint32_t jitter_ms;
    uint32_t check;
} settings_record_t;

/* Same words without jitter_ms; records written by older firmware load with jitter off. */
#define V1_WORDS 9U

static uint32_t checksum_words(const uint32_t *words, size_t count)
{
    uint32_t sum = 0xA5A5A5A5UL;
    for (size_t i = 0; i < count - 1U; i++) sum ^= words[i] + (uint32_t)i;
    return sum;
}

static uint32_t checksum(const settings_record_t *r)
{
    return checksum_words((const uint32_t *)r, sizeof(*r) / 4U);
}

bool qorvo_settings_load(qorvo_radio_state_t *state)
{
    const settings_record_t *r = (const settings_record_t *)SETTINGS_ADDR;
    const uint32_t *words = (const uint32_t *)SETTINGS_ADDR;
    bool v1 = r->magic == SETTINGS_MAGIC_V1 && words[V1_WORDS - 1U] == checksum_words(words, V1_WORDS);
    if (!v1 && (r->magic != SETTINGS_MAGIC || r->check != checksum(r))) return false;
    state->period_ms = r->period_ms;
    state->channel = r->channel;
    state->preamble = r->preamble;
    state->data_rate = r->data_rate;
    state->tx_power = r->tx_power;
    state->tx_ant_dly = r->tx_ant_dly;
    state->rx_ant_dly = r->rx_ant_dly;
    state->jitter_ms = v1 ? 0U : r->jitter_ms;
    return true;
}

bool qorvo_settings_save(const qorvo_radio_state_t *state)
{
    settings_record_t r = {
        .magic = SETTINGS_MAGIC, .period_ms = state->period_ms, .channel = state->channel,
        .preamble = state->preamble, .data_rate = state->data_rate, .tx_power = state->tx_power,
        .tx_ant_dly = state->tx_ant_dly, .rx_ant_dly = state->rx_ant_dly,
        .jitter_ms = state->jitter_ms,
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
