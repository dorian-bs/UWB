#include "qorvo_control.h"

#include <inttypes.h>
#include <stdio.h>
#include <string.h>

static qorvo_radio_state_t state;
static uint8_t board_id;
static bool rx_role, config_failed;
static char line[128], response[384];
static size_t line_len, response_len;
static bool overflow, bad_ascii, frame_ready;

static bool decimal(const char *word, uint32_t *value)
{
    uint32_t n = 0;
    if (*word == '\0') return false;
    for (; *word; word++) {
        if (*word < '0' || *word > '9') return false;
        uint32_t digit = (uint32_t)(*word - '0');
        if (n > (UINT32_MAX - digit) / 10U) return false;
        n = n * 10U + digit;
    }
    *value = n;
    return true;
}

static bool valid(const qorvo_radio_state_t *s)
{
    return s->revision > 0 && s->period_ms >= 100 && s->period_ms <= 10000 &&
        (s->channel == 5 || s->channel == 9) &&
        (s->preamble == 64 || s->preamble == 128 || s->preamble == 256 ||
         s->preamble == 512 || s->preamble == 1024) &&
        (s->data_rate == 850 || s->data_rate == 6800) &&
        s->tx_ant_dly <= UINT16_MAX && s->rx_ant_dly <= UINT16_MAX;
}

static void acknowledge(uint32_t id, const char *error)
{
    qorvo_control_read(&state);
    int n = snprintf(response, sizeof(response),
        "{\"type\":\"control\",\"v\":1,\"id\":%" PRIu32 ",\"ok\":%s,"
        "\"board\":%u,\"role\":\"%s\",\"revision\":%" PRIu32 ","
        "\"period_ms\":%" PRIu32 ",\"channel\":%" PRIu32 ",\"preamble\":%" PRIu32 ","
        "\"data_rate\":%" PRIu32 ",\"tx_power\":%" PRIu32 ",\"running\":%s,"
        "\"tx_ant_dly\":%" PRIu32 ",\"rx_ant_dly\":%" PRIu32 "%s%s%s}\n",
        id, error ? "false" : "true", board_id, rx_role ? "rx" : "tx",
        state.revision, state.period_ms, state.channel, state.preamble, state.data_rate,
        state.tx_power, state.running ? "true" : "false",
        state.tx_ant_dly, state.rx_ant_dly,
        error ? ",\"error\":\"" : "", error ? error : "", error ? "\"" : "");
    response_len = n > 0 && (size_t)n < sizeof(response) ? (size_t)n : 0;
}

static void command(void)
{
    char *words[12];
    size_t count = 0;
    char *p = line;
    uint32_t id = 0;
    const char *error = NULL;
    while (*p) {
        while (*p == ' ' || *p == '\r' || *p == '\t') p++;
        if (!*p) break;
        if (count == sizeof(words)/sizeof(words[0])) break;
        words[count++] = p;
        while (*p && *p != ' ' && *p != '\r' && *p != '\t') p++;
        if (*p) *p++ = '\0';
    }
    if (count >= 2) (void)decimal(words[1], &id);
    if (overflow) error = "frame_overflow";
    else if (bad_ascii || count < 3 || count > 11 || strcmp(words[0], "UWB") ||
             !decimal(words[1], &id)) error = "invalid_command";
    else if (!strcmp(words[2], "GET") && count == 3) {
        /* Readback is performed for every ACK, including failures. */
    } else if (!strcmp(words[2], "PAUSE") && count == 3) {
        qorvo_control_pause();
        state.running = false;
    } else if (!strcmp(words[2], "RESUME") && count == 3) {
        if (config_failed) error = "radio_config_failed";
        else if (!state.running) {
            if (qorvo_control_resume()) state.running = true;
            else error = "resume_failed";
        }
    } else if (!strcmp(words[2], "SET") && (count == 9 || count == 11)) {
        qorvo_radio_state_t requested = state;
        /* Legacy SET preserves the current hardware delays, not cached defaults. */
        qorvo_control_read(&requested);
        if (!decimal(words[3], &requested.revision) ||
            !decimal(words[4], &requested.period_ms) ||
            !decimal(words[5], &requested.channel) ||
            !decimal(words[6], &requested.preamble) ||
            !decimal(words[7], &requested.data_rate) ||
            !decimal(words[8], &requested.tx_power) ||
            (count == 11 && (!decimal(words[9], &requested.tx_ant_dly) ||
                             !decimal(words[10], &requested.rx_ant_dly))) ||
            !valid(&requested))
            error = "invalid_config";
        else if (state.running) error = "must_pause";
        else if (!qorvo_control_apply(&requested)) {
            config_failed = true;
            error = "radio_config_failed";
        } else {
            state = requested;
            config_failed = false;
            (void)qorvo_settings_save(&state);
        }
    } else error = "invalid_command";
    acknowledge(id, error);
    line_len = 0;
    overflow = bad_ascii = frame_ready = false;
}

void qorvo_control_reset_transport(void)
{
    line_len = response_len = 0;
    overflow = bad_ascii = frame_ready = false;
}

void qorvo_control_init(uint8_t board, bool responder)
{
    board_id = board;
    rx_role = responder;
    state = (qorvo_radio_state_t){.period_ms=1000, .channel=9, .preamble=128,
                                .data_rate=6800, .running=true};
    qorvo_control_reset_transport();
    config_failed = false;
    qorvo_control_read(&state);

    qorvo_radio_state_t saved = state;
    saved.revision = 1;
    if (qorvo_settings_load(&saved) && valid(&saved)) {
        qorvo_control_pause();
        if (qorvo_control_apply(&saved)) {
            state = saved;
            state.revision = 0;
            state.running = qorvo_control_resume();
        } else {
            config_failed = true;
            state.running = false;
        }
        qorvo_control_read(&state);
    }
}

size_t qorvo_control_receive(const uint8_t *data, size_t len)
{
    size_t consumed = 0;
    if (response_len || frame_ready) return 0;
    while (consumed < len) {
        uint8_t c = data[consumed++];
        if (c == '\n') {
            line[line_len] = '\0';
            frame_ready = true;
            break;
        }
        if (c < 32 && c != '\r' && c != '\t') bad_ascii = true;
        if (c > 126) bad_ascii = true;
        if (line_len < sizeof(line)-1) line[line_len++] = (char)c;
        else overflow = true;
    }
    return consumed;
}

void qorvo_control_process(void)
{
    if (frame_ready) command();
    if (response_len && qorvo_control_send(response, response_len)) response_len = 0;
}

bool qorvo_control_ack_pending(void) { return response_len != 0 || frame_ready; }
const qorvo_radio_state_t *qorvo_control_state(void) { return &state; }
