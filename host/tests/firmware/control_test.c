#include "qorvo_control.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

static char ack[512];
static bool usb_busy;
static bool fail_apply;
static bool fail_resume;
static unsigned pauses, applies, resumes;
static qorvo_radio_state_t hardware;

bool qorvo_control_send(const char *data, size_t len)
{
    if (usb_busy) return false;
    assert(len < sizeof(ack));
    memcpy(ack, data, len);
    ack[len] = '\0';
    return true;
}

void qorvo_control_read(qorvo_radio_state_t *state)
{
    state->channel = hardware.channel;
    state->preamble = hardware.preamble;
    state->data_rate = hardware.data_rate;
    state->tx_power = hardware.tx_power;
    state->tx_ant_dly = hardware.tx_ant_dly;
    state->rx_ant_dly = hardware.rx_ant_dly;
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
bool qorvo_control_resume(void) { resumes++; return !fail_resume; }

static uint32_t diag_flags = 99;
static bool diag_emit;
static unsigned diag_calls;
void qorvo_diag_set(uint32_t flags, bool emit) { diag_flags = flags; diag_emit = emit; diag_calls++; }

bool qorvo_control_apply(const qorvo_radio_state_t *requested)
{
    applies++;
    hardware.channel = requested->channel;
    hardware.preamble = requested->preamble;
    hardware.data_rate = requested->data_rate;
    if (fail_apply) return false;
    hardware.tx_power = requested->tx_power;
    hardware.tx_ant_dly = requested->tx_ant_dly;
    hardware.rx_ant_dly = requested->rx_ant_dly;
    return true;
}

static void feed(const char *line)
{
    size_t len = strlen(line);
    assert(qorvo_control_receive((const uint8_t *)line, len) == len);
    qorvo_control_process();
}

static void expect(const char *text) { assert(strstr(ack, text)); }

int main(void)
{
    hardware = (qorvo_radio_state_t){.channel=9, .preamble=128, .data_rate=6800,
                                   .tx_power=0x12345678, .tx_ant_dly=16380, .rx_ant_dly=16380};
    qorvo_control_init(1, false);
    feed("UWB 42 GE");
    assert(ack[0] == '\0');
    feed("T\n");
    expect("\"id\":42,\"ok\":true");
    expect("\"role\":\"tx\"");
    expect("\"revision\":0,\"period_ms\":1000");
    expect("\"tx_power\":305419896,\"running\":true");
    expect("\"tx_ant_dly\":16380,\"rx_ant_dly\":16380");

    /* DIAG is accepted while running and never touches the radio configuration. */
    feed("UWB 70 DIAG 5\n");
    expect("\"id\":70,\"ok\":true");
    assert(diag_flags == 5 && diag_emit && diag_calls == 1);
    feed("UWB 71 DIAG OFF\n");
    expect("\"ok\":true");
    assert(diag_flags == 0 && !diag_emit && diag_calls == 2);
    feed("UWB 72 DIAG 16\n");
    expect("\"error\":\"invalid_config\"");
    feed("UWB 73 DIAG\n");
    expect("\"error\":\"invalid_command\"");
    assert(diag_calls == 2 && applies == 0 && pauses == 0);

    feed("UWB 43 SET 1 100 5 256 850 4294967295 12345 23456\n");
    expect("\"error\":\"must_pause\"");
    assert(applies == 0);
    feed("UWB 43 SET 1 100 5 256 850 4294967295\n");
    expect("\"error\":\"must_pause\"");
    assert(applies == 0);
    feed("UWB 44 PAUSE\n");
    expect("\"running\":false");
    assert(pauses == 1);
    feed("UWB 45 SET 1 100 5 256 850 4294967295\n");
    expect("\"revision\":1,\"period_ms\":100,\"channel\":5");
    expect("\"preamble\":256,\"data_rate\":850,\"tx_power\":4294967295");
    expect("\"tx_ant_dly\":16380,\"rx_ant_dly\":16380");
    assert(applies == 1);
    feed("UWB 46 RESUME\n");
    expect("\"running\":true");
    assert(resumes == 1);
    feed("UWB 47 PAUSE\n");

    const char *invalid[] = {
        "UWB 1 SET 0 100 9 128 6800 1\n",
        "UWB 1 SET 2 9 9 128 6800 1\n",
        "UWB 1 SET 2 10001 9 128 6800 1\n",
        "UWB 1 SET 2 100 7 128 6800 1\n",
        "UWB 1 SET 2 100 9 127 6800 1\n",
        "UWB 1 SET 2 100 9 128 6810 1\n",
        "UWB 1 SET 2 100 9 128 6800 4294967296\n",
        "UWB 1 SET 2 100 9 128 6800 -1\n",
        "UWB 1 SET 2 100 9 128 6800 0x10\n",
        "UWB 4294967296 GET\n", "UWB -1 GET\n", "UWB 2 GET EXTRA\n",
        "UWB 2 SET 2 100 9 128 6800\n", "UWB 2 NOPE\n"
    };
    for (size_t i=0; i<sizeof(invalid)/sizeof(invalid[0]); i++) {
        feed(invalid[i]);
        expect("\"ok\":false");
        assert(applies == 1);
        assert(qorvo_control_state()->revision == 1);
    }

    fail_apply = true;
    feed("UWB 48 SET 2 200 9 512 6800 1\n");
    expect("\"ok\":false");
    expect("\"channel\":9,\"preamble\":512");
    expect("\"revision\":1,\"period_ms\":100");
    expect("\"tx_power\":4294967295,\"running\":false");
    feed("UWB 49 RESUME\n");
    expect("\"error\":\"radio_config_failed\"");
    assert(resumes == 1);
    fail_apply = false;
    feed("UWB 50 SET 2 10000 9 1024 850 0\n");
    expect("\"ok\":true");
    expect("\"revision\":2,\"period_ms\":10000");
    fail_resume = true;
    feed("UWB 51 RESUME\n");
    expect("\"error\":\"resume_failed\"");
    expect("\"running\":false");
    fail_resume = false;
    feed("UWB 52 RESUME\n");
    expect("\"running\":true");

    usb_busy = true;
    feed("UWB 53 PAUSE\n");
    assert(qorvo_control_ack_pending());
    const char *two = "UWB 54 GET\nUWB 55 GET\n";
    assert(qorvo_control_receive((const uint8_t *)two, strlen(two)) == 0);
    unsigned count = pauses;
    qorvo_control_process();
    assert(pauses == count);
    usb_busy = false;
    qorvo_control_process();
    expect("\"id\":53");
    size_t n = qorvo_control_receive((const uint8_t *)two, strlen(two));
    assert(n == strlen("UWB 54 GET\n"));
    qorvo_control_process();
    expect("\"id\":54");
    assert(qorvo_control_receive((const uint8_t *)two+n, strlen(two)-n) == strlen(two)-n);
    qorvo_control_process();
    expect("\"id\":55");

    char longline[256];
    memset(longline, 'x', sizeof(longline));
    memcpy(longline, "UWB 56 GET ", 11);
    longline[254] = '\n';
    longline[255] = '\0';
    feed(longline);
    expect("\"error\":\"frame_overflow\"");
    expect("\"id\":56");
    feed("UWB 57 GET\n");
    expect("\"id\":57,\"ok\":true");
    const uint8_t nul[] = {'U','W','B',' ','5','8',' ','G','E','T',0,'\n'};
    assert(qorvo_control_receive(nul, sizeof(nul)) == sizeof(nul));
    qorvo_control_process();
    expect("\"ok\":false");
    qorvo_control_init(2, true);
    feed("UWB 0 GET\n");
    expect("\"id\":0,\"ok\":true,\"board\":2,\"role\":\"rx\"");
    feed("UWB 59 PAUSE\n");
    feed("UWB 60 SET 1 100 9 64 850 42\n");
    expect("\"id\":60,\"ok\":true");
    expect("\"revision\":1,\"period_ms\":100,\"channel\":9");
    expect("\"preamble\":64,\"data_rate\":850,\"tx_power\":42,\"running\":false");
    assert(qorvo_control_state()->revision == 1);

    feed("UWB 61 SET 2 200 5 256 6800 24 12345 23456\n");
    expect("\"id\":61,\"ok\":true");
    expect("\"tx_ant_dly\":12345,\"rx_ant_dly\":23456");
    feed("UWB 62 SET 3 300 9 128 850 42\n");
    expect("\"id\":62,\"ok\":true");
    expect("\"tx_ant_dly\":12345,\"rx_ant_dly\":23456");

    unsigned previous_applies = applies;
    const char *invalid_delays[] = {
        "UWB 63 SET 4 100 9 128 6800 1 65536 0\n",
        "UWB 63 SET 4 100 9 128 6800 1 0 65536\n",
        "UWB 63 SET 4 100 9 128 6800 1 4294967296 0\n",
        "UWB 63 SET 4 100 9 128 6800 1 0 4294967296\n",
        "UWB 63 SET 4 100 9 128 6800 1 -1 0\n",
        "UWB 63 SET 4 100 9 128 6800 1 0 -1\n",
        "UWB 63 SET 4 100 9 128 6800 1 +1 0\n",
        "UWB 63 SET 4 100 9 128 6800 1 0 +1\n",
        "UWB 63 SET 4 100 9 128 6800 1 0x10 0\n",
        "UWB 63 SET 4 100 9 128 6800 1 0 0x10\n",
        "UWB 63 SET 4 100 9 128 6800 1 1.0 0\n",
        "UWB 63 SET 4 100 9 128 6800 1 0 1.0\n",
        "UWB 63 SET 4 100 9 128 6800 1 12345\n",
        "UWB 63 SET 4 100 9 128 6800 1 12345 23456 EXTRA\n",
        "UWB 63 SET 4 100 9 128 6800 1 12345 23456 1 2 3\n"
    };
    for (size_t i=0; i<sizeof(invalid_delays)/sizeof(invalid_delays[0]); i++) {
        feed(invalid_delays[i]);
        expect("\"ok\":false");
        expect("\"tx_ant_dly\":12345,\"rx_ant_dly\":23456");
        assert(applies == previous_applies);
        assert(qorvo_control_state()->revision == 3);
    }
    feed("UWB 64 SET 4 100 9 128 6800 1 0");
    assert(applies == previous_applies);
    feed(" 65535\n");
    expect("\"ok\":true");
    expect("\"tx_ant_dly\":0,\"rx_ant_dly\":65535");
    feed("UWB 65 SET 5 100 9 128 6800 1 65535 0\n");
    expect("\"ok\":true");
    expect("\"tx_ant_dly\":65535,\"rx_ant_dly\":0");
    fail_apply = true;
    feed("UWB 66 SET 6 200 5 256 850 42 11111 22222\n");
    expect("\"error\":\"radio_config_failed\"");
    expect("\"revision\":5,\"period_ms\":100");
    expect("\"tx_ant_dly\":65535,\"rx_ant_dly\":0");
    feed("UWB 67 RESUME\n");
    expect("\"error\":\"radio_config_failed\"");
    fail_apply = false;

    hardware.tx_ant_dly = 13579;
    hardware.rx_ant_dly = 24680;
    feed("UWB 68 GET\n");
    expect("\"tx_ant_dly\":13579,\"rx_ant_dly\":24680");
    hardware.tx_ant_dly = 11111;
    hardware.rx_ant_dly = 22222;
    feed("UWB 69 SET 6 100 9 128 6800 1\n");
    expect("\"id\":69,\"ok\":true");
    expect("\"tx_ant_dly\":11111,\"rx_ant_dly\":22222");
    assert(hardware.tx_ant_dly == 11111 && hardware.rx_ant_dly == 22222);
    feed("UWB 70 RESUME\n");
    expect("\"ok\":true");
    expect("\"running\":true,\"tx_ant_dly\":11111,\"rx_ant_dly\":22222");

    /* Settings saved by the last successful SET are restored and applied at boot. */
    assert(saves > 0 && saved_state.tx_ant_dly == 11111 && saved_state.rx_ant_dly == 22222);
    saved_state.period_ms = 500; saved_state.channel = 5; saved_state.preamble = 256;
    saved_state.data_rate = 850; saved_state.tx_power = 0x1234; saved_state.tx_ant_dly = 111;
    saved_state.rx_ant_dly = 222;
    unsigned boot_applies = applies;
    qorvo_control_init(1, false);
    assert(applies == boot_applies + 1);
    feed("UWB 71 GET\n");
    expect("\"revision\":0,\"period_ms\":500,\"channel\":5,\"preamble\":256");
    expect("\"data_rate\":850,\"tx_power\":4660,\"running\":true");
    expect("\"tx_ant_dly\":111,\"rx_ant_dly\":222");
    fail_apply = true;
    qorvo_control_init(1, false);
    feed("UWB 72 RESUME\n");
    expect("\"error\":\"radio_config_failed\"");
    fail_apply = false;
    saved_state.channel = 7;
    boot_applies = applies;
    qorvo_control_init(1, false);
    assert(applies == boot_applies);
    puts("native control tests passed");
    return 0;
}
