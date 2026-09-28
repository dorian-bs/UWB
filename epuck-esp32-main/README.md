# ESP32 UWB Bridge

This firmware forwards STM32 UART data to one TCP client and forwards TCP data
back to the STM32. The default TCP port is 3333.

## Build

Install ESP-IDF, open an ESP-IDF-enabled terminal, then run:

```bash
idf.py menuconfig
idf.py build
idf.py flash monitor
```

In `menuconfig`, open **UWB bridge configuration** and set the Wi-Fi SSID,
password, and TCP port. These values are stored in the ignored `sdkconfig`, not
in source control.

If `command -v idf.py` has no output, source ESP-IDF's setup script first:

```bash
source /path/to/esp-idf/export.sh
```

The bridge UART is UART1 at 115200 baud, with TX on GPIO 43 and RX on GPIO 44.