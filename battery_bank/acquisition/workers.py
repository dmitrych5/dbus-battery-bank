"""Runs the blocking serial pollers on their own threads, one per port, handing immutable
snapshots to the GLib thread through locked outboxes.

The main loop must never touch a serial port: a blocking read that outlives the cycle
interval leaves the loop no idle time and starves incoming D-Bus dispatch (vrmlogger's
device scan then times out and silently drops the services from VRM). Each worker owns its
poller exclusively, so packs sharing a port stay strictly sequential on the wire exactly as
before; everything crossing the thread boundary is immutable or copied under the outbox
lock. Commands flow the other way through the same lock: SoC resets are enqueued here and
forwarded to the poller on its own thread between passes.

A worker never dies on its own: pass exceptions are logged and counted, and the main loop
escalates persistent failure the same way it escalates its own cycle failures."""

import logging
import threading
import time
from typing import Callable

from battery_bank.acquisition.battery_poller import PackPoller
from battery_bank.acquisition.shunt_poller import ShuntPoller
from battery_bank.acquisition.snapshots import PackInfo
from battery_bank.config import BatteryPortConfig
from battery_bank.core.values import BatterySnapshot, ShuntSnapshot

logger = logging.getLogger(__name__)

DISCOVERY_RETRY_SECONDS = 30.0
"""How long a battery worker waits before re-running discovery for still-missing packs."""
STOP_JOIN_TIMEOUT_SECONDS = 5.0
"""A worker blocked in a long serial wait (e.g. discovery interference retries) is abandoned
as a daemon thread rather than delaying shutdown longer than this."""


class SerialWorker:
    """Base: a stoppable daemon thread running _pass_once about every pass_interval_seconds,
    starting each pass on the interval grid when passes are fast and back-to-back when the
    serial work runs longer."""

    def __init__(self, name: str, pass_interval_seconds: float, clock: Callable[[], float] = time.monotonic):
        self._name = name
        self._pass_interval_seconds = pass_interval_seconds
        self._clock = clock
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._consecutive_failures = 0
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(STOP_JOIN_TIMEOUT_SECONDS)
        if self._thread.is_alive():
            logger.warning("%s did not stop within %.0f s; abandoning the daemon thread", self._name, STOP_JOIN_TIMEOUT_SECONDS)

    def consecutive_failures(self) -> int:
        with self._lock:
            return self._consecutive_failures

    def _run(self) -> None:
        while not self._stop.is_set():
            started = self._clock()
            self._tick()
            self._stop.wait(max(0.0, self._pass_interval_seconds - (self._clock() - started)))

    def _tick(self) -> None:
        """One guarded pass: an exception is a failure to count and log, never a dead worker."""
        try:
            self._pass_once()
            with self._lock:
                self._consecutive_failures = 0
        except Exception:
            logger.exception("%s: pass failed", self._name)
            with self._lock:
                self._consecutive_failures += 1

    def _pass_once(self) -> None:
        raise NotImplementedError


class BatteryPortWorker(SerialWorker):
    """Owns one battery port's PackPoller: discovery with periodic retries while packs are
    missing, one poll pass per interval, and forwarding of queued SoC resets."""

    def __init__(
        self,
        port_config: BatteryPortConfig,
        poller: PackPoller,
        pass_interval_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ):
        super().__init__(f"battery-poller-{port_config.device.rsplit('/', 1)[-1]}", pass_interval_seconds, clock)
        self._poller = poller
        self._expected_pack_count = len(port_config.pack_addresses)
        self._infos: dict[str, PackInfo] = {}
        self._snapshots: dict[str, BatterySnapshot] = {}
        self._resettable: frozenset[str] = frozenset()
        self._pending_soc_resets: list[tuple[str, float]] = []
        self._next_discovery_at = 0.0

    def infos(self) -> dict[str, PackInfo]:
        with self._lock:
            return dict(self._infos)

    def snapshots(self) -> dict[str, BatterySnapshot]:
        """The latest snapshot per discovered pack; a pack that failed its last polls keeps
        its old snapshot here, and the control core judges staleness by its timestamp."""
        with self._lock:
            return dict(self._snapshots)

    def request_soc_reset(self, unique_id: str, soc_percent: float) -> bool:
        """Same acceptance rules as PackPoller.request_soc_reset, judged against the last
        pass's exported view; the accepted write reaches the pack on the worker thread
        before its next poll."""
        if not 0 <= soc_percent <= 100:
            return False
        with self._lock:
            if unique_id not in self._resettable:
                return False
            self._pending_soc_resets.append((unique_id, soc_percent))
        return True

    def _pass_once(self) -> None:
        infos = None
        if len(self._infos) < self._expected_pack_count and self._clock() >= self._next_discovery_at:
            self._next_discovery_at = self._clock() + DISCOVERY_RETRY_SECONDS
            infos = self._poller.discover()
        with self._lock:
            pending, self._pending_soc_resets = self._pending_soc_resets, []
        for unique_id, soc_percent in pending:
            if not self._poller.request_soc_reset(unique_id, soc_percent):
                # Accepted against the last pass's view but refused now (e.g. SetSoc just
                # exhausted its write attempts) — must not go down silently.
                logger.warning("SoC reset refused for pack %s", unique_id)
        snapshots = self._poller.poll()
        with self._lock:
            if infos is not None:
                # The poller keys by port address; everything downstream keys by unique_id.
                self._infos = {info.unique_id: info for info in infos.values()}
            for snapshot in snapshots:
                self._snapshots[snapshot.identity.unique_id] = snapshot
            self._resettable = self._poller.resettable_pack_ids()


class ShuntWorker(SerialWorker):
    """Owns the shunt's VE.Direct port; each pass drains the stream and exports the latest
    snapshot."""

    def __init__(self, poller: ShuntPoller, pass_interval_seconds: float, clock: Callable[[], float] = time.monotonic):
        super().__init__("shunt-poller", pass_interval_seconds, clock)
        self._poller = poller
        self._snapshot: ShuntSnapshot | None = None

    def snapshot(self) -> ShuntSnapshot | None:
        with self._lock:
            return self._snapshot

    def _pass_once(self) -> None:
        snapshot = self._poller.poll()
        with self._lock:
            self._snapshot = snapshot
