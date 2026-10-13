# Lab notes (template)

From the repository root, copy this to `LAB_NOTES.md` (gitignored) and fill in your own values.

```bash
cp docs/LAB_NOTES.example.md LAB_NOTES.md
```

Keep real addresses and serial paths in `LAB_NOTES.md` only, never in tracked docs or source.
Keep credentials in the untracked ESP32 `sdkconfig`, not in lab notes.

## Boards

| Board | USB serial (macOS) | ESP32 IP | Bridge port |
| --- | --- | --- | --- |
| BOARD_A | `/dev/tty.usbmodemXXXX` | `192.168.x.x` | `3333` |
| BOARD_B | `/dev/tty.usbmodemYYYY` | `192.168.x.y` | `3333` |

Find the serial path with `ls /dev/tty.usbmodem*`, and the ESP32 IP from its boot log
(`Got IP: ...`) via `bash scripts/esp32.sh -p /dev/cu.usbmodemXXXX monitor`.

## Wi-Fi

Do not record the SSID or password in any file. Set them in the untracked ESP32 `sdkconfig`:

```bash
make esp-config   # UWB bridge configuration -> Wi-Fi SSID / Wi-Fi password
```
