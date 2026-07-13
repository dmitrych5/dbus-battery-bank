"""D-Bus service objects for the aggregate bank and the per-pack read-only services.

Each service lives on its own private D-Bus connection: D-Bus object paths are per-connection,
not per service name, so multiple services with identical path layouts cannot share one — this
is why connections are constructed directly instead of via the shared dbus.SystemBus()
singleton (a trap that will resurface if "simplified").

Service names, ProductIds, and DeviceInstances deliberately match the previous stack
(aggregate: com.victronenergy.battery.aggregate, instance 99, ProductId 0xBA44; packs:
serialbattery settings prefix and ProductId 0xBA77) so VRM history continues seamlessly.
"""

import logging
import os
import platform
import sys

import dbus

_VELIB_PYTHON_CANDIDATES = (
    os.path.join(os.path.dirname(__file__), "..", "..", "ext", "velib_python"),
    "/opt/victronenergy/dbus-systemcalc-py/ext/velib_python",
)
for _candidate in _VELIB_PYTHON_CANDIDATES:
    if os.path.isdir(_candidate):
        sys.path.insert(1, _candidate)
        break

from settingsdevice import SettingsDevice  # noqa: E402
from vedbus import VeDbusService  # noqa: E402

from battery_bank import __version__  # noqa: E402

logger = logging.getLogger(__name__)

AGGREGATE_SERVICE_NAME = "com.victronenergy.battery.aggregate"
AGGREGATE_DEVICE_INSTANCE = 99
AGGREGATE_PRODUCT_ID = 0xBA44
PACK_PRODUCT_ID = 0xBA77
PACK_SETTINGS_PREFIX = "serialbattery_"
"""Kept from the previous stack so each pack reclaims its existing VRM DeviceInstance."""


def private_bus_connection() -> dbus.bus.BusConnection:
    bus_type = dbus.bus.BusConnection.TYPE_SESSION if "DBUS_SESSION_BUS_ADDRESS" in os.environ else dbus.bus.BusConnection.TYPE_SYSTEM
    return dbus.bus.BusConnection(bus_type)


def claim_pack_device_instance(unique_id: str) -> int:
    """Claims (or reclaims) a VRM device instance for a pack via localsettings, using the
    previous stack's settings path so existing instances — and with them VRM history — are
    reused. localsettings assigns the next free instance for previously unseen packs."""
    settings_id = "".join(character if character.isalnum() else "_" for character in unique_id)
    settings = SettingsDevice(
        private_bus_connection(),
        {"instance": [f"/Settings/Devices/{PACK_SETTINGS_PREFIX}{settings_id}/ClassAndVrmInstance", "battery:1", 0, 0]},
        eventCallback=None,
    )
    return int(settings["instance"].split(":")[1])


class DbusBatteryService:
    """One com.victronenergy.battery service. Paths are fixed at creation from the initial
    values; update() pushes new values for existing paths."""

    def __init__(
        self,
        service_name: str,
        device_instance: int,
        product_id: int,
        product_name: str,
        hardware_version: str | None,
        serial: str,
        initial_values: dict[str, object],
        writable_paths: dict[str, object] | None = None,
    ):
        self._service = VeDbusService(service_name, private_bus_connection(), register=False)
        self._service.add_path("/Mgmt/ProcessName", "dbus-battery-bank")
        self._service.add_path("/Mgmt/ProcessVersion", f"{__version__} on Python {platform.python_version()}")
        self._service.add_path("/Mgmt/Connection", "Serial")
        self._service.add_path("/DeviceInstance", device_instance)
        self._service.add_path("/ProductId", product_id)
        self._service.add_path("/ProductName", product_name)
        self._service.add_path("/CustomName", product_name, writeable=True)
        self._service.add_path("/FirmwareVersion", __version__)
        self._service.add_path("/HardwareVersion", hardware_version)
        self._service.add_path("/Serial", serial)
        self._service.add_path("/Connected", 1)
        self._service.add_path("/State", 0, writeable=True)
        self._service.add_path("/ErrorCode", 0, writeable=True)
        for path, value in initial_values.items():
            self._service.add_path(path, value, writeable=True)
        for path, on_change in (writable_paths or {}).items():
            self._service.add_path(path, 0, writeable=True, onchangecallback=on_change)
        self._service.register()
        logger.info("Registered %s (DeviceInstance %d)", service_name, device_instance)

    def update(self, values: dict[str, object]) -> None:
        with self._service as batch:
            for path, value in values.items():
                batch[path] = value

    def set_error(self, state: int, error_code: int) -> None:
        with self._service as batch:
            batch["/State"] = state
            batch["/ErrorCode"] = error_code
