"""Reads raw VE.Direct text protocol data directly from the serial port, to extract shunt
measurements before any D-Bus processing by Venus OS. Direct reading gives higher current
resolution than the shunt's D-Bus service (1 mA as sent over the wire)."""

import logging
import time
from typing import Protocol

from battery_bank.core.values import ShuntHistoryTotals, ShuntSnapshot
from battery_bank.transport.vedirect import VeDirectParser, parse_history_totals, parse_shunt_reading

logger = logging.getLogger(__name__)

SHUNT_BAUD_RATE = 19200
SHUNT_SERIAL_TIMEOUT_SECONDS = 0.5


class Link(Protocol):
    def read_available(self) -> bytes: ...

    def interference_detected(self) -> bool: ...

    def reopen(self) -> None: ...


class ShuntPoller:
    def __init__(self, link: Link, clock=time.monotonic):
        self._link = link
        self._clock = clock
        self._parser = VeDirectParser()
        self._latest: ShuntSnapshot | None = None
        self._history_totals: ShuntHistoryTotals | None = None

    def poll(self) -> ShuntSnapshot | None:
        """Feeds newly arrived bytes to the parser and returns the latest valid snapshot; the
        control core judges freshness by the snapshot timestamp. The device alternates between
        a measurement frame and a history frame, so each snapshot (made from a measurement
        frame) carries the lifetime totals from the last history frame seen."""
        if self._link.interference_detected():
            # On interference, reopen the port to reset the port settings.
            logger.warning("Interference detected, reopening serial port")
            self._link.reopen()
        for frame in self._parser.feed(self._link.read_available()):
            totals = parse_history_totals(frame)
            if totals is not None:
                self._history_totals = ShuntHistoryTotals(
                    charged_energy_kwh=totals.charged_energy_kwh,
                    discharged_energy_kwh=totals.discharged_energy_kwh,
                    total_ah_drawn_ah=totals.total_ah_drawn_ah,
                    full_discharge_count=totals.full_discharge_count,
                    automatic_sync_count=totals.automatic_sync_count,
                )
            reading = parse_shunt_reading(frame)
            if reading is not None:
                self._latest = ShuntSnapshot(
                    taken_at_monotonic=self._clock(),
                    current_amps=reading.current_amps,
                    soc_percent=reading.soc_percent,
                    consumed_ah=reading.consumed_ah,
                    aux_voltage_volts=reading.aux_voltage_volts,
                    history_totals=self._history_totals,
                )
        return self._latest
