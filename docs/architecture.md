# Architecture and contract

This file is the single source of truth for names shared between the Python
integration, the dashboard card and the docs. Change it first, then the code.

## Layout

```
custom_components/anova_ble/
  __init__.py          setup, unload, services and card registration in async_setup
  manifest.json        (owned by maintainer; frontend and http are after_dependencies)
  const.py             (owned by maintainer, add constants freely)
  config_flow.py       bluetooth discovery, user pick, options flow
  coordinator.py       DataUpdateCoordinator[AnovaState]
  entity.py            AnovaEntity base class
  climate.py sensor.py binary_sensor.py number.py button.py select.py
  diagnostics.py
  services.yaml strings.json icons.json translations/en.json translations/nl.json
  frontend/__init__.py  registers the card (static path + extra JS URL)
  frontend/anova-cooker-card.js
  protocol/            HA free BLE client (see docs/protocol.md)
tests/
  protocol/            unit tests for the protocol package
  conftest.py test_*.py  HA tests (pytest-homeassistant-custom-component)
```

Target: Home Assistant 2026.10, Python 3.14. Use only the HA Bluetooth stack
(`homeassistant.components.bluetooth`), never BlueZ directly.

## Protocol API (`custom_components/anova_ble/protocol`)

```python
from .protocol import (
    AnovaClient, AnovaState, AnovaDeviceInfo, DeviceStatus, TemperatureUnit,
    TimerState, AnovaError, AnovaConnectionError, AnovaTimeoutError,
    AnovaCommandError, AnovaResponseError, AnovaValueError,
    SERVICE_UUID, LOCAL_NAME, TEMP_MIN_C, TEMP_MAX_C, TEMP_MIN_F, TEMP_MAX_F,
    TIMER_MIN_MINUTES, TIMER_MAX_MINUTES,
)

client = AnovaClient(ble_device, disconnect_delay=2.0)  # None = stay connected
client.set_ble_device(ble_device)          # call before each poll
await client.read_state() -> AnovaState
await client.read_device_info() -> AnovaDeviceInfo   # best effort, once
await client.set_target_temperature(56.5, unit=TemperatureUnit.CELSIUS) -> float
await client.set_unit(TemperatureUnit.FAHRENHEIT) -> TemperatureUnit
await client.set_timer(120) -> int
await client.start(); await client.stop()
await client.start_timer(); await client.stop_timer()
await client.start_cook(56.5, minutes=120, unit=TemperatureUnit.CELSIUS)
await client.disconnect()
client.address, client.name, client.is_connected, client.unit

AnovaState(status: DeviceStatus | None, raw_status: str, unit: TemperatureUnit,
           current_temperature: float, target_temperature: float,
           timer: TimerState(minutes: int, running: bool)); state.is_cooking
AnovaDeviceInfo(id_card: str | None, firmware_version: str | None)
           .has_wifi, .model
DeviceStatus values: running stopped low_water heater_error power_loss
                     user_change_parameter
TemperatureUnit values: "c" "f"; .min_temperature .max_temperature
```

All errors derive from `AnovaError`. Transport problems are
`AnovaConnectionError` (timeouts are a subclass). Out of range input is
`AnovaValueError` (also a `ValueError`). The client serializes commands
itself, callers do not need a lock.

## Options

| Key | Values | Default |
| --- | --- | --- |
| `poll_interval` | seconds, 5 to 300 | 15 |
| `connection_mode` | `per_poll`, `persistent` | `per_poll` |

`per_poll`: `AnovaClient(disconnect_delay=2.0)`. The link closes right after
each poll, so other apps can connect in between. Costs a reconnect per poll.
`persistent`: `AnovaClient(disconnect_delay=None)`. Faster and fewer
reconnects, but the cooker accepts only one BLE connection, so the phone app
cannot connect while Home Assistant holds it.

## Coordinator rules

The cooker is switched off most of the time. Being unreachable is normal and
never an error: setup never fails on it and nothing is logged above INFO.

* `DataUpdateCoordinator[AnovaState | None]`, `update_interval = poll_interval`.
* Setup loads the last state and device info from `AnovaStore`
  (`.storage/anova_ble.<entry_id>`) and never waits for the cooker. The first
  poll runs as a background task. `async_remove_entry` deletes the store.
* Before each poll: `bluetooth.async_ble_device_from_address(hass, address,
  connectable=True)`. None means out of range: the cooker is offline at once.
* A failed connect or command counts as a failed poll. After
  `FAILURES_BEFORE_OFFLINE` (3) in a row the cooker is offline.
* A failed poll returns the last data (frozen). `UpdateFailed` is never
  raised, so entities stay available. `coordinator.online` tells the state.
* Going offline and coming back are logged once each at INFO. Single failed
  polls only at DEBUG.
* Backoff: while failing, `update_interval = min(poll_interval * 2**failures,
  MAX_BACKOFF_INTERVAL)`. Reset to `poll_interval` on success. The first
  advertisement after an out of range poll triggers a poll at once.
* A successful poll saves the state (delayed write, 60 s). Device info is read
  once after the first successful poll and written to the device registry.

## Entities

All entities: `_attr_has_entity_name = True`, `translation_key` as below,
`unique_id = f"{address}_{key}"` (address from `entry.unique_id`, upper case),
device info with `connections={(CONNECTION_BLUETOOTH, address)}`,
manufacturer `Anova Culinary`, model from `AnovaDeviceInfo.model`,
`sw_version` from firmware. Sensors, binary sensors and climate show the
last known values while offline (climate `hvac_action` is None then).
Buttons, number and select are unavailable while offline.
`PARALLEL_UPDATES = 0` on read only platforms,
`1` on platforms with actions.

| Platform | key / translation_key | Notes |
| --- | --- | --- |
| climate | `cooker` | `_attr_name = None` (device name). Modes off, heat. Unit from cooker. Step 0.1, precision tenths. Min and max from `TemperatureUnit`. hvac_action: off when not cooking, heating when current < target minus 0.5, else idle |
| sensor | `current_temperature` | temperature, measurement, cooker unit |
| sensor | `target_temperature` | temperature, cooker unit |
| sensor | `timer_remaining` | duration, minutes |
| sensor | `status` | enum, options = `DeviceStatus` values plus `offline`; `offline` while the cooker is unreachable, None if unknown |
| sensor | `rssi` | signal strength, dBm, diagnostic, disabled by default, from `bluetooth.async_last_service_info` |
| binary_sensor | `online` | device class connectivity, on while the cooker answers |
| binary_sensor | `cooking` | device class running |
| binary_sensor | `timer_running` | device class running, timer counting down |
| number | `timer` | 0 to 6000 min, step 1, box mode, duration device class, sets the timer |
| button | `start_timer`, `stop_timer` | |
| select | `temperature_unit` | options `celsius`, `fahrenheit`, config category |

## Services (entity services on the climate entity)

Registered in `async_setup` with
`homeassistant.helpers.service.async_register_platform_entity_service`
(`entity_domain="climate"`).

| Service | Fields | Behaviour |
| --- | --- | --- |
| `anova_ble.start_cook` | `temperature` (required, float, in the Home Assistant unit system), `duration` (optional, time period) | Converts temperature to the cooker unit, minutes = duration rounded up to whole minutes, calls `client.start_cook` |
| `anova_ble.stop_cook` | none | `client.stop()` |

Target: `entity_id` of the `anova_ble` climate entity.

## Dashboard card

* File: `custom_components/anova_ble/frontend/anova-cooker-card.js`.
* Served at `/anova_ble/anova-cooker-card.js?v=<manifest version>`, added with
  `frontend.add_extra_js_url`. Users do nothing.
* Custom element `anova-cooker-card`, editor `anova-cooker-card-editor`.
* Config: `{ type: "custom:anova-cooker-card", entity: "climate.xxx", name?: string }`.
* The card finds sibling entities through `hass.entities[climate].device_id`
  and the `translation_key` of each entity on the same device
  (`timer`, `timer_remaining`, `start_timer`, `stop_timer`, `cooking`,
  `timer_running`, `online`, `status`). The card counts as offline when the
  climate entity is unavailable, `online` is off or `status` is `offline`,
  and then shows the last values greyed out with every button disabled.
* Actions it calls: `climate.set_temperature`, `anova_ble.start_cook`,
  `anova_ble.stop_cook`, `number.set_value` (timer), `button.press`.
