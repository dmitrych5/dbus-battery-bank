"""Service entry point: wires config, persistence, pollers, the control core, and the D-Bus
services into the GLib main loop. All decisions live in the layers below; this module only
moves data between them and applies the error taxonomy at the process level:

- self-heal: a failed poll or a failed cycle is logged and retried next cycle
- latch: carried inside ControlState and persisted, untouched here
- report & restart: repeated cycle failures or an invalid config publish an error state (so
  VRM alarms) and exit for the daemontools supervisor
"""

import dataclasses
import logging
import signal
import sys
import time
from pathlib import Path

from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

from battery_bank.acquisition.battery_poller import SERIAL_TIMEOUT_SECONDS, PackPoller
from battery_bank.acquisition.shunt_poller import SHUNT_BAUD_RATE, SHUNT_SERIAL_TIMEOUT_SECONDS, ShuntPoller
from battery_bank.acquisition.snapshots import PackInfo
from battery_bank.config import Config, ConfigError, load_config
from battery_bank.core.bank import BankDecision, BankInputs, ControlState, EventSeverity, step_bank
from battery_bank.core.history import HistoryState, clear_history, step_history
from battery_bank.core.protections import reset_trips
from battery_bank.core.values import BatterySnapshot
from battery_bank.persistence.state_file import PersistedState, StateFile, StateFileError, restore_control_state, to_persisted
from battery_bank.publishing import dbus_services
from battery_bank.publishing.diagnostics_text import diagnostics_values
from battery_bank.publishing.service_values import aggregate_service_values, history_service_values, pack_service_values
from battery_bank.transport.serial_link import SerialLink

logger = logging.getLogger("battery_bank")

BATTERY_BAUD_RATE = 9600
POLL_INTERVAL_SECONDS = 1
DISCOVERY_RETRY_SECONDS = 30
THERMAL_SAVE_INTERVAL_SECONDS = 2 * 3600.0
"""How often the PTC thermal filter state is worth a flash write; must stay comfortably below
THERMAL_RESTORE_MAX_AGE_SECONDS."""
HISTORY_SAVE_INTERVAL_SECONDS = 30 * 60.0
"""The history's energy integrals grow every cycle, so a fresh snapshot reaches the flash only
this often; a crash loses at most this much accumulation, and a clean shutdown saves the final
values regardless."""
MAX_CONSECUTIVE_CYCLE_FAILURES = 30
CYCLE_FAILURES_BEFORE_ALARM = 5
SOC_RESET_PERCENT = 100.0

VICTRON_STATE_ERROR = 10
VICTRON_ERROR_CODE_SETTINGS_INVALID = 119

EVENT_LOG_LEVELS = {EventSeverity.INFO: logging.INFO, EventSeverity.WARNING: logging.WARNING, EventSeverity.ERROR: logging.ERROR}

DEFAULT_APP_DIR = Path("/data/apps/dbus-battery-bank")


class BatteryBankService:
    def __init__(self, config: Config, state_file: StateFile):
        self._config = config
        self._state_file = state_file
        # _restore_state() also initializes the persistence trackers (_written_thermal,
        # _history and friends), so it must not be preceded by defaults that would clobber it.
        self._state = self._restore_state()
        self._pack_pollers = [
            PackPoller(port, SerialLink(port.device, BATTERY_BAUD_RATE, SERIAL_TIMEOUT_SECONDS), config.cells_per_pack)
            for port in config.battery_ports
        ]
        self._shunt_poller = (
            ShuntPoller(SerialLink(config.shunt_port, SHUNT_BAUD_RATE, SHUNT_SERIAL_TIMEOUT_SECONDS)) if config.shunt_port is not None else None
        )
        self._snapshots: dict[str, BatterySnapshot] = {}
        self._pack_infos: dict[str, PackInfo] = {}
        self._aggregate_service: dbus_services.DbusBatteryService | None = None
        self._pack_services: dict[str, dbus_services.DbusBatteryService] = {}
        self._consecutive_cycle_failures = 0
        self._mainloop = GLib.MainLoop()
        self._service_internal_alarm = False
        """Raises /Alarms/InternalFailure on the aggregate for faults of this service itself
        (corrupt state file, repeated cycle failures): logs alone never reach the operator.
        Stays raised until the service restarts cleanly."""

    def run(self) -> None:
        self._discover()
        GLib.timeout_add_seconds(POLL_INTERVAL_SECONDS, self._cycle)
        GLib.timeout_add_seconds(DISCOVERY_RETRY_SECONDS, self._discover)
        signal.signal(signal.SIGTERM, lambda *_: self._mainloop.quit())
        signal.signal(signal.SIGINT, lambda *_: self._mainloop.quit())
        self._mainloop.run()
        # A clean shutdown flushes the cadence-limited history, so routine restarts lose nothing.
        self._persist(fresh_history_due=True)
        logger.info("Main loop stopped, exiting")

    def _restore_state(self) -> ControlState:
        try:
            persisted = self._state_file.load()
        except StateFileError:
            # Fail loud: a lost state file may have cleared safety latches. The log line alone
            # would never be noticed, so the internal-failure alarm reaches VRM too.
            logger.exception("State file was corrupt; starting with defaults — any latched protection trips were LOST")
            self._service_internal_alarm = True
            persisted = PersistedState()
        self._written_thermal = persisted.thermal
        self._history = HistoryState(values=persisted.history)
        self._written_history = persisted.history
        self._history_written_at_wall = time.time()
        return restore_control_state(persisted, self._config, time.monotonic(), time.time())

    def _discover(self) -> bool:
        for poller in self._pack_pollers:
            for info in poller.discover().values():
                if info.unique_id not in self._pack_infos:
                    self._pack_infos[info.unique_id] = info
        expected = sum(len(port.pack_addresses) for port in self._config.battery_ports)
        # Keep retrying periodically until every configured pack is found; step_bank alarms
        # about the incomplete picture once the startup grace expires.
        return len(self._pack_infos) < expected

    def _cycle(self) -> bool:
        try:
            self._cycle_inner()
            self._consecutive_cycle_failures = 0
        except Exception:
            logger.exception("Cycle failed")
            self._consecutive_cycle_failures += 1
            if self._consecutive_cycle_failures >= CYCLE_FAILURES_BEFORE_ALARM:
                self._service_internal_alarm = True
            if self._consecutive_cycle_failures >= MAX_CONSECUTIVE_CYCLE_FAILURES:
                logger.error("%d consecutive cycle failures; exiting for the supervisor to restart", self._consecutive_cycle_failures)
                if self._aggregate_service is not None:
                    self._aggregate_service.set_error(VICTRON_STATE_ERROR, 0)
                self._mainloop.quit()
        return True

    def _cycle_inner(self) -> None:
        for poller in self._pack_pollers:
            for snapshot in poller.poll():
                self._snapshots[snapshot.identity.unique_id] = snapshot
        shunt = self._shunt_poller.poll() if self._shunt_poller is not None else None

        inputs = BankInputs(packs=tuple(self._snapshots.values()), shunt=shunt)
        now_monotonic = time.monotonic()
        self._state, decision, events = step_bank(self._config, self._state, inputs, now_monotonic)
        self._history = step_history(self._history, decision, inputs.packs, now_monotonic, time.time())
        for event in events:
            logger.log(EVENT_LOG_LEVELS[event.severity], event.message)
        for unique_id in decision.request_soc_reset_pack_ids:
            self._request_soc_reset(unique_id)

        self._persist()
        self._publish(decision, inputs)

    def _aggregate_values(self, decision: BankDecision, inputs: BankInputs) -> dict[str, object]:
        values = aggregate_service_values(self._config, decision, inputs.packs, inputs.shunt, self._service_internal_alarm)
        values.update(diagnostics_values(self._config, self._state, decision, inputs.packs, inputs.shunt, time.monotonic()))
        values.update(history_service_values(self._history.values, time.time()))
        return values

    def _publish(self, decision: BankDecision, inputs: BankInputs) -> None:
        # Registering only once the bank is ready means a restarting service never publishes
        # zero limits during warmup (see the startup grace in core.bank).
        if decision.ready and self._aggregate_service is None:
            self._aggregate_service = dbus_services.DbusBatteryService(
                service_name=dbus_services.AGGREGATE_SERVICE_NAME,
                device_instance=dbus_services.AGGREGATE_DEVICE_INSTANCE,
                product_id=dbus_services.AGGREGATE_PRODUCT_ID,
                product_name="Battery Bank",
                hardware_version=None,
                serial="battery-bank",
                initial_values=self._aggregate_values(decision, inputs),
                writable_paths={
                    "/Settings/ResetProtectionTrips": self._reset_trips_callback,
                    "/History/Clear": self._clear_history_callback,
                },
                settings=dbus_services.DeviceSettings(dbus_services.AGGREGATE_SETTINGS_GROUP, claim_instance=False),
            )
        if decision.ready:
            for snapshot in inputs.packs:
                self._ensure_pack_service(snapshot, decision)

        if self._aggregate_service is not None:
            self._aggregate_service.update(self._aggregate_values(decision, inputs))
        for unique_id, service in self._pack_services.items():
            snapshot = self._snapshots.get(unique_id)
            if snapshot is not None:
                service.update(pack_service_values(self._config, decision, snapshot))

    def _ensure_pack_service(self, snapshot: BatterySnapshot, decision: BankDecision) -> None:
        unique_id = snapshot.identity.unique_id
        if unique_id in self._pack_services:
            return
        info = self._pack_infos.get(unique_id)
        port_basename = snapshot.identity.port.rsplit("/", 1)[-1]
        settings = dbus_services.DeviceSettings(dbus_services.pack_settings_group(unique_id), claim_instance=True)
        self._pack_services[unique_id] = dbus_services.DbusBatteryService(
            service_name=f"com.victronenergy.battery.{port_basename}__0x{snapshot.identity.address:02x}",
            device_instance=settings.device_instance,
            product_id=dbus_services.PACK_PRODUCT_ID,
            product_name="JBD UP16S",
            hardware_version=info.hardware_description if info is not None else None,
            serial=unique_id,
            initial_values=pack_service_values(self._config, decision, snapshot),
            writable_paths={"/Settings/ResetSocTo": lambda path, value, uid=unique_id: self._request_soc_reset(uid, value)},
            settings=settings,
        )

    def _request_soc_reset(self, unique_id: str, soc_percent: float = SOC_RESET_PERCENT) -> bool:
        for poller in self._pack_pollers:
            if poller.request_soc_reset(unique_id, soc_percent):
                logger.info("SoC reset to %.1f%% requested for pack %s", soc_percent, unique_id)
                return True
        logger.warning("SoC reset refused for pack %s", unique_id)
        return False

    def _reset_trips_callback(self, path: str, value) -> bool:
        if not value:
            return False
        logger.warning("Operator reset of latched protection trips")
        self._state = dataclasses.replace(self._state, protections=reset_trips(self._state.protections))
        self._persist()
        return True

    def _clear_history_callback(self, path: str, value) -> bool:
        if not value:
            return False
        logger.warning("Operator cleared history (category %s)", value)
        self._history = clear_history(self._history, int(value))
        # Saved immediately: an operator action must not be resurrected by a crash.
        self._persist(fresh_history_due=True)
        return True

    def _persist(self, fresh_history_due: bool = False) -> None:
        now_wall = time.time()
        persisted = to_persisted(self._state, self._history.values, now_wall)
        # The thermal filter changes every sample and the history's integrals every cycle;
        # rewriting them more often than their save cadences would wear the flash without a
        # matching benefit. Between refreshes the last-written copies keep the file stable.
        thermal_refresh_due = (
            persisted.thermal is not None
            and (self._written_thermal is None or persisted.thermal.saved_at_wall_seconds - self._written_thermal.saved_at_wall_seconds >= THERMAL_SAVE_INTERVAL_SECONDS)
        )
        if not thermal_refresh_due:
            persisted = dataclasses.replace(persisted, thermal=self._written_thermal)
        fresh_history_due = fresh_history_due or now_wall - self._history_written_at_wall >= HISTORY_SAVE_INTERVAL_SECONDS
        if not fresh_history_due:
            persisted = dataclasses.replace(persisted, history=self._written_history)
        if self._state_file.save(persisted):
            self._written_thermal = persisted.thermal
            self._written_history = persisted.history
            if fresh_history_due:
                self._history_written_at_wall = now_wall


def publish_config_error_and_wait(error: ConfigError) -> None:
    """Fail loud on invalid configuration: register the aggregate service in an error state so
    VRM raises a notification, and stay up (not controlling anything) for the operator."""
    for issue in error.issues:
        logger.error("Config: %s", issue)
    service = dbus_services.DbusBatteryService(
        service_name=dbus_services.AGGREGATE_SERVICE_NAME,
        device_instance=dbus_services.AGGREGATE_DEVICE_INSTANCE,
        product_id=dbus_services.AGGREGATE_PRODUCT_ID,
        product_name="Battery Bank",
        hardware_version=None,
        serial="battery-bank",
        # The alarm path guarantees a VRM notification even if the error state alone does not
        # produce one.
        initial_values={"/Info/ChargeLimitation": "Invalid configuration, see the log", "/Alarms/InternalFailure": 2},
    )
    service.set_error(VICTRON_STATE_ERROR, VICTRON_ERROR_CODE_SETTINGS_INVALID)
    GLib.MainLoop().run()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    app_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_APP_DIR
    DBusGMainLoop(set_as_default=True)

    logger.info("Starting dbus-battery-bank from %s", app_dir)
    try:
        config = load_config(app_dir / "config.ini")
    except ConfigError as error:
        publish_config_error_and_wait(error)
        return

    BatteryBankService(config, StateFile(app_dir / "state.json")).run()


if __name__ == "__main__":
    main()
