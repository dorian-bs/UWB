#include "qorvo_radio_control.h"
#include "deca_device_api.h"
#include "dw3000_deca_regs.h"
#include <string.h>

/* Qorvo's reference PG delay is 0x34 for both channels. Keep channel-specific
 * measured settings once available; never replace boot RF calibration on GET. */
static uint8_t pg_delay[2] = {0x34, 0x34};

void qorvo_radio_capture_rf(void)
{
    unsigned channel = (dwt_read_reg(CHAN_CTRL_ID) & CHAN_CTRL_RF_CHAN_BIT_MASK) != 0;
    pg_delay[channel] = dwt_readpgdelay();
}

void qorvo_radio_read(qorvo_radio_state_t *state)
{
    state->channel = (dwt_read_reg(CHAN_CTRL_ID) & CHAN_CTRL_RF_CHAN_BIT_MASK) ? 9U : 5U;
    uint32_t fine = (dwt_read_reg(TX_FCTRL_HI_ID) & TX_FCTRL_HI_FINE_PLEN_BIT_MASK)
                    >> TX_FCTRL_HI_FINE_PLEN_BIT_OFFSET;
    state->preamble = (fine + 1U) * 8U;
    state->data_rate = (dwt_read_reg(TX_FCTRL_ID) & TX_FCTRL_TXBR_BIT_MASK) ? 6800U : 850U;
    state->tx_power = dwt_read_reg(TX_POWER_ID);
    state->tx_ant_dly = dwt_gettxantennadelay();
    state->rx_ant_dly = dwt_getrxantennadelay();
}

bool qorvo_radio_apply(const qorvo_radio_state_t *requested)
{
    if (requested->tx_ant_dly > UINT16_MAX || requested->rx_ant_dly > UINT16_MAX) return false;
    dwt_config_t config;
    memset(&config, 0, sizeof(config));
    config.chan = (uint8_t)requested->channel;
    config.txPreambLength = (uint16_t)requested->preamble;
    unsigned pac;
    if (requested->preamble == 64) { config.rxPAC = DWT_PAC4; pac = 4; }
    else if (requested->preamble == 128) { config.rxPAC = DWT_PAC8; pac = 8; }
    else if (requested->preamble == 256) { config.rxPAC = DWT_PAC16; pac = 16; }
    else { config.rxPAC = DWT_PAC32; pac = 32; }
    config.txCode = config.rxCode = 9U;
    config.dataRate = requested->data_rate == 850 ? DWT_BR_850K : DWT_BR_6M8;
    config.sfdType = requested->data_rate == 850 ? DWT_SFD_DW_16 : DWT_SFD_IEEE_4A;
    unsigned sfd = requested->data_rate == 850 ? 16U : 8U;
    config.sfdTO = (uint16_t)(requested->preamble + 1U + sfd - pac);
    config.phrMode = DWT_PHRMODE_STD;
    config.phrRate = DWT_PHRRATE_STD;
    config.stsMode = DWT_STS_MODE_OFF;
    config.stsLength = DWT_STS_LEN_64;
    config.pdoaMode = DWT_PDOA_M0;
    if (dwt_configure(&config) != DWT_SUCCESS) return false;
    dwt_txconfig_t txrf = {.PGdly=pg_delay[requested->channel == 9],
                          .power=requested->tx_power, .PGcount=0};
    dwt_configuretxrf(&txrf);
    dwt_settxantennadelay((uint16_t)requested->tx_ant_dly);
    dwt_setrxantennadelay((uint16_t)requested->rx_ant_dly);
    qorvo_radio_capture_rf();
    qorvo_radio_state_t actual = {0};
    qorvo_radio_read(&actual);
    return actual.channel == requested->channel && actual.preamble == requested->preamble &&
           actual.data_rate == requested->data_rate && actual.tx_power == requested->tx_power &&
           actual.tx_ant_dly == requested->tx_ant_dly && actual.rx_ant_dly == requested->rx_ant_dly;
}
