#include "qorvo_radio_control.h"
#include "deca_device_api.h"
#include "dw3000_deca_regs.h"
#include <assert.h>
#include <stdio.h>

static uint32_t channel_reg = 1, fctrl = TX_FCTRL_TXBR_BIT_MASK;
static uint32_t fine = 15U << TX_FCTRL_HI_FINE_PLEN_BIT_OFFSET;
static uint32_t power = 0x12345678;
static uint8_t pgdelay = 0x31;
static bool failed;
static unsigned rf_calls;
static dwt_config_t config;
static dwt_txconfig_t rf;

uint32_t dwt_read_reg(uint32_t address)
{
    if (address == CHAN_CTRL_ID) return channel_reg;
    if (address == TX_FCTRL_ID) return fctrl;
    if (address == TX_FCTRL_HI_ID) return fine;
    assert(address == TX_POWER_ID);
    return power;
}
uint8_t dwt_readpgdelay(void) { return pgdelay; }
int32_t dwt_configure(dwt_config_t *requested)
{
    config = *requested;
    /* Deliberately model partial driver writes before a calibration failure. */
    channel_reg = config.chan == 9;
    fine = ((config.txPreambLength / 8U)-1U) << TX_FCTRL_HI_FINE_PLEN_BIT_OFFSET;
    fctrl = config.dataRate == DWT_BR_6M8 ? TX_FCTRL_TXBR_BIT_MASK : 0;
    return failed ? DWT_ERROR : DWT_SUCCESS;
}
void dwt_configuretxrf(dwt_txconfig_t *requested)
{
    rf_calls++;
    rf = *requested;
    power = rf.power;
    pgdelay = rf.PGdly;
}

int main(void)
{
    qorvo_radio_state_t actual = {0};
    qorvo_radio_capture_rf();
    qorvo_radio_read(&actual);
    assert(actual.channel == 9 && actual.preamble == 128 && actual.data_rate == 6800);
    assert(actual.tx_power == 0x12345678);
    qorvo_radio_state_t requested = {.channel=5, .preamble=256, .data_rate=850, .tx_power=0};
    assert(qorvo_radio_apply(&requested));
    assert(config.rxPAC == DWT_PAC16 && config.sfdTO == 257);
    assert(config.sfdType == DWT_SFD_DW_16);
    assert(config.txCode == 9 && config.rxCode == 9);
    assert(config.stsMode == DWT_STS_MODE_OFF && config.phrMode == DWT_PHRMODE_STD);
    assert(rf.PGdly == 0x34 && rf.PGcount == 0 && rf.power == 0);
    requested.channel = 9;
    requested.preamble = 64;
    requested.data_rate = 6800;
    requested.tx_power = UINT32_MAX;
    assert(qorvo_radio_apply(&requested));
    assert(config.rxPAC == DWT_PAC4 && config.sfdTO == 69);
    assert(config.sfdType == DWT_SFD_IEEE_4A);
    assert(rf.PGdly == 0x31 && rf.power == UINT32_MAX);
    const unsigned preambles[] = {128, 512, 1024};
    const dwt_pac_size_e pacs[] = {DWT_PAC8, DWT_PAC32, DWT_PAC32};
    for (unsigned i=0; i<3; i++) {
        requested.preamble = preambles[i];
        assert(qorvo_radio_apply(&requested));
        assert(config.rxPAC == pacs[i]);
    }
    requested.preamble = 64;
    requested.data_rate = 850;
    assert(qorvo_radio_apply(&requested));
    assert(config.rxPAC == DWT_PAC4 && config.sfdTO == 77);
    assert(config.sfdType == DWT_SFD_DW_16);
    failed = true;
    requested.channel = 5;
    requested.preamble = 256;
    requested.data_rate = 850;
    unsigned calls = rf_calls;
    assert(!qorvo_radio_apply(&requested));
    assert(rf_calls == calls);
    qorvo_radio_read(&actual);
    assert(actual.channel == 5 && actual.preamble == 256 && actual.data_rate == 850);
    assert(actual.tx_power == UINT32_MAX);
    puts("native radio tests passed");
    return 0;
}
