# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-11-16

### Changed

* The default connection mode is now keep connected. Anova ended app support for these cookers, so there is no phone app left to make room for, and one open link is more reliable than a reconnect for every poll. Cookers where the options were never saved switch to keep connected with this update. Per poll is still available in the options.
* While Bluetooth still sees the cooker but connecting fails, for example on a weak signal, Home Assistant now tries again at least once a minute instead of waiting up to 5 minutes.
* The setup and help texts no longer point at the Anova phone app. They ask to close any other app that is connected to the cooker.

### Added

* An original icon for the integration (`brand/icon.png`).

### Fixed

* README: the Dutch section said entities become unavailable on a weak signal. Sensors keep their last values and only the controls are greyed out.
* README: the feature list now names all three binary sensors.

## [0.1.0] - 2026-11-04

### Added

* Initial release for the original Anova Precision Cooker, 800 W (Bluetooth only) and 900 W (Bluetooth and WiFi).
* Config flow with Bluetooth discovery and manual setup.
* Climate entity to set the target temperature and to turn heating on or off.
* Sensors for current temperature, target temperature, timer remaining, status and signal strength (RSSI, disabled by default).
* Binary sensors for the cooking state, the timer and the connection.
* A switched off cooker is not an error: setup always succeeds, the status shows offline, sensors keep their last known values (stored across restarts) and controls are greyed out until the cooker is back.
* Polls start at once when the cooker is seen again.
* Works on Home Assistant 2026.10 and 2026.11 (native climate temperature properties, install through HACS or the new Marketplace).
* Clears the cooker's input line after connecting, which fixed "Invalid Command" answers on a real cooker.
* Timer number entity and start and stop timer buttons.
* Select entity to switch the cooker between Celsius and Fahrenheit.
* Actions `anova_ble.start_cook` and `anova_ble.stop_cook`.
* Options for poll interval and connection mode (per poll or keep connected).
* Diagnostics download.
* Dashboard card that loads automatically.
* Works with local Bluetooth adapters and ESPHome Bluetooth proxies.
* English and Dutch translations.

[Unreleased]: https://github.com/gielk/ha-anova/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/gielk/ha-anova/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/gielk/ha-anova/releases/tag/v0.1.0
