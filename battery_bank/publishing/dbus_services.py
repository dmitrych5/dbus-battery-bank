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


class DeviceSettings:
    """VRM device instance and custom name via localsettings, reusing the previous stack's
    settings paths so existing instances and names — and with them VRM history — carry over.
    localsettings assigns the next free instance for previously unseen devices."""

    def __init__(self, settings_group: str, claim_instance: bool):
        supported = {"custom_name": [f"/Settings/Devices/{settings_group}/CustomName", "", 0, 0]}
        if claim_instance:
            supported["instance"] = [f"/Settings/Devices/{settings_group}/ClassAndVrmInstance", "battery:1", 0, 0]
        self._settings = SettingsDevice(private_bus_connection(), supported, eventCallback=None)

    @property
    def device_instance(self) -> int:
        return int(self._settings["instance"].split(":")[1])

    @property
    def custom_name(self) -> str | None:
        return self._settings["custom_name"] or None

    def store_custom_name(self, name: str) -> None:
        self._settings["custom_name"] = name


def pack_settings_group(unique_id: str) -> str:
    return PACK_SETTINGS_PREFIX + "".join(character if character.isalnum() else "_" for character in unique_id)


AGGREGATE_SETTINGS_GROUP = "aggregatebatteries"


class DbusBatteryService:
    """One com.victronenergy.battery service. Paths are fixed at creation from the initial
    values; update() pushes new values for existing paths."""

    STATE_RUNNING = 9

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
        settings: DeviceSettings | None = None,
    ):
        self._settings = settings
        self._command_paths = tuple(writable_paths or ())
        self._service = VeDbusService(service_name, private_bus_connection(), register=False)
        self._service.add_path("/Mgmt/ProcessName", "dbus-battery-bank")
        self._service.add_path("/Mgmt/ProcessVersion", f"{__version__} on Python {platform.python_version()}")
        self._service.add_path("/Mgmt/Connection", "Serial")
        self._service.add_path("/DeviceInstance", device_instance)
        self._service.add_path("/ProductId", product_id)
        self._service.add_path("/ProductName", product_name)
        custom_name = (settings.custom_name if settings is not None else None) or product_name
        self._service.add_path("/CustomName", custom_name, writeable=True, onchangecallback=self._custom_name_changed)
        self._service.add_path("/FirmwareVersion", __version__)
        self._service.add_path("/HardwareVersion", hardware_version)
        self._service.add_path("/Serial", serial)
        self._service.add_path("/Connected", 1)
        self._service.add_path("/State", self.STATE_RUNNING, writeable=True)
        # None until a real error, so the GUI hides the error row.
        self._service.add_path("/ErrorCode", None, writeable=True)
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
            # Re-arm command paths: vedbus's SetValue silently skips writes equal to the
            # current value without calling the callback, so a command value left latched
            # (e.g. ResetProtectionTrips stuck at 1) would make every following identical
            # command a no-op. Setting an already-zero path is signal-free.
            for path in self._command_paths:
                batch[path] = 0

    def set_error(self, state: int, error_code: int) -> None:
        with self._service as batch:
            batch["/State"] = state
            batch["/ErrorCode"] = error_code

    def _custom_name_changed(self, path: str, value: str):
        if self._settings is not None:
            self._settings.store_custom_name(value)
        return value
