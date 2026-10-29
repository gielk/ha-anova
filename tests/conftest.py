"""Fixtures for the Anova Precision Cooker (Bluetooth) tests."""

from __future__ import annotations

from collections.abc import Generator
import time
from unittest.mock import MagicMock, patch

from bleak.backends.device import BLEDevice
from habluetooth import BluetoothServiceInfoBleak
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.anova_ble.const import DOMAIN
from custom_components.anova_ble.protocol import (
    SERVICE_UUID,
    AnovaDeviceInfo,
    AnovaState,
    DeviceStatus,
    TemperatureUnit,
    TimerState,
)

from .const import ADDRESS, TITLE

BLE_MODULE = "homeassistant.components.bluetooth"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    request: pytest.FixtureRequest,
) -> None:
    """Load the custom integration in every Home Assistant test.

    The protocol tests do not use Home Assistant, so they are left alone.
    """
    if "protocol" not in request.path.parts:
        request.getfixturevalue("enable_custom_integrations")


@pytest.fixture(autouse=True)
def auto_mock_bluetooth(request: pytest.FixtureRequest) -> None:
    """Keep the Bluetooth stack from touching real adapters."""
    if "protocol" not in request.path.parts:
        request.getfixturevalue("mock_bluetooth")


def make_service_info(
    name: str, address: str = ADDRESS, rssi: int = -60
) -> BluetoothServiceInfoBleak:
    """Build the service info of an advertisement that offers service ffe0."""
    return BluetoothServiceInfoBleak(
        name=name,
        address=address,
        rssi=rssi,
        manufacturer_data={},
        service_data={},
        service_uuids=[SERVICE_UUID],
        source="local",
        device=BLEDevice(address, name, {}),
        advertisement=None,
        connectable=True,
        time=time.monotonic(),
        tx_power=None,
    )


@pytest.fixture
def anova_service_info() -> BluetoothServiceInfoBleak:
    """Return the advertisement of an Anova cooker."""
    return make_service_info("Anova")


@pytest.fixture
def scale_service_info() -> BluetoothServiceInfoBleak:
    """Return the advertisement of a scale that also offers service ffe0."""
    return make_service_info("SBF70", address="AA:BB:CC:DD:EE:FF")


@pytest.fixture
def ble_device(anova_service_info: BluetoothServiceInfoBleak) -> BLEDevice:
    """Return the BLEDevice of the cooker."""
    return anova_service_info.device


@pytest.fixture
def sample_state() -> AnovaState:
    """Return a cooker that is heating towards 56.5 °C with a running timer."""
    return AnovaState(
        status=DeviceStatus.RUNNING,
        raw_status="running",
        unit=TemperatureUnit.CELSIUS,
        current_temperature=54.2,
        target_temperature=56.5,
        timer=TimerState(minutes=90, running=True),
    )


@pytest.fixture
def sample_device_info() -> AnovaDeviceInfo:
    """Return the device info of a BT+WiFi cooker."""
    return AnovaDeviceInfo(id_card="anova f56-123456", firmware_version="1.2.3")


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a config entry for the cooker."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=TITLE,
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS},
    )


@pytest.fixture
def mock_client_cls() -> Generator[MagicMock]:
    """Patch AnovaClient where the integration and the config flow import it."""
    with (
        patch("custom_components.anova_ble.AnovaClient", autospec=True) as client_cls,
        patch("custom_components.anova_ble.config_flow.AnovaClient", new=client_cls),
    ):
        yield client_cls


@pytest.fixture
def mock_client(
    mock_client_cls: MagicMock,
    sample_state: AnovaState,
    sample_device_info: AnovaDeviceInfo,
) -> MagicMock:
    """Return the mocked client instance, answering like the sample cooker."""
    client = mock_client_cls.return_value
    client.read_state.return_value = sample_state
    client.read_device_info.return_value = sample_device_info
    client.is_connected = False
    client.unit = sample_state.unit
    return client


@pytest.fixture
def mock_ble_device(ble_device: BLEDevice) -> Generator[MagicMock]:
    """Make the Bluetooth stack report the cooker as in range."""
    with patch(
        f"{BLE_MODULE}.async_ble_device_from_address", return_value=ble_device
    ) as mock:
        yield mock


@pytest.fixture
def mock_setup_entry() -> Generator[MagicMock]:
    """Skip the real setup after a config flow created an entry."""
    with patch(
        "custom_components.anova_ble.async_setup_entry", return_value=True
    ) as mock:
        yield mock


@pytest.fixture
async def init_integration(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    mock_ble_device: MagicMock,
) -> MockConfigEntry:
    """Set up the integration with a mocked client."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry
