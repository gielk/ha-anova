# Anova Precision Cooker BLE protocol (original BT and BT+WiFi models)

This is the protocol as implemented in `custom_components/anova_ble/protocol/`.
Every detail below was cross checked against these sources. When they disagree,
the official Anova material wins.

| Ref | Source |
| --- | --- |
| A | Official Anova CLI: [anova-culinary/developer-project-a2a3](https://github.com/anova-culinary/developer-project-a2a3) and the [developer docs](https://developer.anovaculinary.com/docs/devices/a2-a3/ble-protocol) |
| B | [c3V6a2Vy/pyanova](https://github.com/c3V6a2Vy/pyanova) |
| C | ESPHome `esphome/components/anova` (`anova_base.cpp`, `anova.cpp`) |
| D | [dfrankland/sous-vide `docs/ble.md`](https://github.com/dfrankland/sous-vide/blob/master/docs/ble.md) (decompiled Android app 2.1.0) |
| E | [CY8s/pycirculate](https://github.com/CY8s/pycirculate) and [jshridha/anovamaster](https://github.com/jshridha/anovamaster) |

## Transport

| Item | Value | Refs |
| --- | --- | --- |
| Advertised local name | `Anova` | A, D |
| Service | `0000ffe0-0000-1000-8000-00805f9b34fb` | A, B, C, D |
| Characteristic (TX and RX) | `0000ffe1-0000-1000-8000-00805f9b34fb` | A, B, C, D |
| Write type | write without response | A (`response=False`), C (`ESP_GATT_WRITE_TYPE_NO_RSP`) |
| Command encoding | ASCII, terminated with `\r` | A, B, C, D |
| Write chunking | at most 20 bytes per write | D |
| Responses | notifications on the same characteristic | A, B, C, D |
| Command timeout | 15 s recommended | A |
| Concurrency | one command at a time, queue with timeout | D |

The `01:02:03:04` address prefix is a firmware quirk shared by many units. It
is not used for matching. Discovery matches local name `Anova*` together with
the `ffe0` service UUID, because generic `ffe0` serial modules (for example
some scales) advertise the same service.

### Response framing (the sources disagree)

* A says a response is complete when a notification chunk is shorter than
  20 bytes or the data ends with `0x00`.
* D says to concatenate chunks until `\r` is received.
* C parses each notification up to the first `\r`.

The client accepts all of them. A response is complete on `\r` or `0x00`.
If a chunk shorter than 20 bytes arrives without a terminator, the client
waits a short grace period (0.3 s) for more data and then treats the buffer
as complete. Surrounding whitespace is stripped.

## Commands used by this integration

| Command | Example response | Notes | Refs |
| --- | --- | --- | --- |
| `status` | `running`, `stopped` | Also seen: `low water`, `heater error`, `power loss` (pycirculate calls it `power interrupt error`), `user change parameter` | A, C, D, E |
| `start` | `start` | Starts heating. Echo is returned even if it did not start | A, C, E |
| `stop` | `stop` | Stops heating. Also stops the timer | A, C, E |
| `read temp` | `23.4` | Current water temperature in the device unit | A, B, C, E |
| `read set temp` | `56.5` | Target temperature in the device unit | A, B, C, E |
| `set temp 56.5` | `56.5` | One decimal. Range 5.0 to 99.9 °C or 41.0 to 211.8 °F | A, B, C, D |
| `read unit` | `c` or `f` | | A, B, C, E |
| `set unit c` | `c` | Lowercase `c` or `f` | A, B, C, E |
| `read timer` | `120 stopped`, `45 running` | Minutes left plus timer state | E |
| `set timer 120` | `120` | Whole minutes, 0 to 6000 | A, B, D, E |
| `start time` | `start time` | Starts the countdown. Cooker must be running | A, B, E |
| `stop time` | `stop time` | Stops the countdown | A, B, E |
| `get id card` | `anova f56-...` | Prefix `anova f56-` means the BT+WiFi 900 W model | A |
| `version` | firmware string | 900 W (WiFi) model only | A |
| `clear alarm` | | 900 W (WiFi) model only. Not exposed in v1 | A |

An unknown command returns `Invalid Command` (B).

### Start a cook (official sequence, A)

1. `set temp <t>`
2. `stop time`
3. `set timer <minutes>` (0 when there is no timer)
4. `start`
5. `start time`, only when minutes > 0

## 800 W versus 900 W

* 800 W: Bluetooth only.
* 900 W: Bluetooth plus WiFi. Supports `version` and `clear alarm`. Its
  `get id card` answer starts with `anova f56-`.
* The BLE command set used for cooking is the same on both.

## Not supported

* Anova Precision Cooker Nano: different BLE protocol.
* Precision Cooker 3.0 and Pro: different protocol. Use the Home Assistant
  core `anova` cloud integration for those.
