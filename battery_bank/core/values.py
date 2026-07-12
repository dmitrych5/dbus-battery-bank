"""Immutable values flowing from the acquisition layer into the control core.

Snapshots are stamped with a monotonic timestamp and carry a stable pack identity, so nothing
downstream keys on serial port or bus address. A missing snapshot or an old timestamp is how
staleness reaches the control core; acquisition never raises into it.
"""

from dataclasses import dataclass
from enum import IntEnum


class AlarmSeverity(IntEnum):
    """Matches the Victron D-Bus alarm value convention."""

    OK = 0
    WARNING = 1
    ALARM = 2


@dataclass(frozen=True)
class PackIdentity:
    unique_id: str
    """BMS model + serial number where available; stable across ports and restarts."""
    port: str
    address: int


@dataclass(frozen=True)
class PackAlarms:
    """BMS fault/alarm flags decoded into Victron alarm categories."""

    high_cell_voltage: AlarmSeverity
    low_cell_voltage: AlarmSeverity
    high_voltage: AlarmSeverity
    low_voltage: AlarmSeverity
    high_charge_current: AlarmSeverity
    high_discharge_current: AlarmSeverity
    high_charge_temperature: AlarmSeverity
    low_charge_temperature: AlarmSeverity
    high_temperature: AlarmSeverity
    low_temperature: AlarmSeverity
    high_internal_temperature: AlarmSeverity
    cell_imbalance: AlarmSeverity
    low_soc: AlarmSeverity
    internal_failure: AlarmSeverity


@dataclass(frozen=True)
class BmsLimits:
    """DVCC-style limits reported by one BMS for itself."""

    charge_current_amps: float
    discharge_current_amps: float
    charge_voltage_volts: float
    discharge_voltage_volts: float


@dataclass(frozen=True)
class ChainAggregatedLimits:
    """Limits a daisy-chain master reports for its whole chain."""

    charge_current_amps: float
    discharge_current_amps: float


@dataclass(frozen=True)
class BatterySnapshot:
    taken_at_monotonic: float
    identity: PackIdentity

    voltage_volts: float
    current_amps: float
    soc_percent: float
    soh_percent: float

    full_capacity_ah: float
    rated_capacity_ah: float
    remaining_capacity_ah: float
    charge_cycles: int
    total_discharge_ah: float | None
    """Lifetime discharge throughput; None when the BMS firmware does not expose it."""

    cell_voltages_volts: tuple[float, ...]
    cells_balancing: tuple[bool, ...]

    cell_temperatures_celsius: tuple[float, ...]
    mosfet_temperature_celsius: float
    ambient_temperature_celsius: float

    charge_fet_enabled: bool
    discharge_fet_enabled: bool

    alarms: PackAlarms
    bms_limits: BmsLimits
    chain_aggregated_limits: ChainAggregatedLimits | None
    """Present only in snapshots taken from a daisy-chain master."""

    def min_cell_voltage_volts(self) -> float:
        return min(self.cell_voltages_volts)

    def max_cell_voltage_volts(self) -> float:
        return max(self.cell_voltages_volts)


@dataclass(frozen=True)
class ShuntSnapshot:
    taken_at_monotonic: float
    current_amps: float
    soc_percent: float
    consumed_ah: float
    aux_voltage_volts: float | None
    """PTC thermistor chain voltage on the shunt's Aux input; None if the shunt does not report it."""
