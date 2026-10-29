# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-10-29

### Added

* Initial release for the original Anova Precision Cooker, 800 W (Bluetooth only) and 900 W (Bluetooth and WiFi).
* Config flow with Bluetooth discovery and manual setup.
* Climate entity to set the target temperature and to turn heating on or off.
* Sensors for current temperature, target temperature, timer remaining, status and signal strength (RSSI, disabled by default).
* Binary sensor for the cooking state.
* Timer number entity and start and stop timer buttons.
* Select entity to switch the cooker between Celsius and Fahrenheit.
* Actions `anova_ble.start_cook` and `anova_ble.stop_cook`.
* Options for poll interval and connection mode (per poll or keep connected).
* Diagnostics download.
* Dashboard card that loads automatically.
* Works with local Bluetooth adapters and ESPHome Bluetooth proxies.
* English and Dutch translations.

[Unreleased]: https://github.com/gielk/ha-anova/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/gielk/ha-anova/releases/tag/v0.1.0
