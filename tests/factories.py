"""Builders for core value objects with sensible healthy-pack defaults, so each test states
only what it cares about."""

from battery_bank.core.values import (
    AlarmSeverity,
    BatterySnapshot,
    BmsLimits,
    ChainAggregatedLimits,
    PackAlarms,
    PackIdentity,
)

ALARM_CATEGORY_NAMES = tuple(PackAlarms.__dataclass_fields__)


def make_alarms(**severities: AlarmSeverity) -> PackAlarms:
    values = {name: AlarmSeverity.OK for name in ALARM_CATEGORY_NAMES}
    values.update(severities)
    return PackAlarms(**values)


def make_snapshot(
    unique_id: str = "pack-1",
    port: str = "/dev/ttyUSB0",
    address: int = 1,
    taken_at_monotonic: float = 1000.0,
    voltage_volts: float = 53.0,
    current_amps: float = 0.0,
    soc_percent: float = 80.0,
    soh_percent: float = 100.0,
    full_capacity_ah: float = 100.0,
    rated_capacity_ah: float = 100.0,
    remaining_capacity_ah: float = 80.0,
    charge_cycles: int = 10,
    total_discharge_ah: float | None = -1000.0,
    cell_voltages_volts: tuple[float, ...] = (3.3,) * 16,
    cells_balancing: tuple[bool, ...] | None = None,
    cell_temperatures_celsius: tuple[float, ...] = (20.0, 20.0, 20.0, 20.0),
    mosfet_temperature_celsius: float = 25.0,
    ambient_temperature_celsius: float = 25.0,
    charge_fet_enabled: bool = True,
    discharge_fet_enabled: bool = True,
    alarms: PackAlarms | None = None,
    bms_limits: BmsLimits | None = None,
    chain_aggregated_limits: ChainAggregatedLimits | None = None,
) -> BatterySnapshot:
    return BatterySnapshot(
        taken_at_monotonic=taken_at_monotonic,
        identity=PackIdentity(unique_id=unique_id, port=port, address=address),
        voltage_volts=voltage_volts,
        current_amps=current_amps,
        soc_percent=soc_percent,
        soh_percent=soh_percent,
        full_capacity_ah=full_capacity_ah,
        rated_capacity_ah=rated_capacity_ah,
        remaining_capacity_ah=remaining_capacity_ah,
        charge_cycles=charge_cycles,
        total_discharge_ah=total_discharge_ah,
        cell_voltages_volts=cell_voltages_volts,
        cells_balancing=cells_balancing if cells_balancing is not None else (False,) * len(cell_voltages_volts),
        cell_temperatures_celsius=cell_temperatures_celsius,
        mosfet_temperature_celsius=mosfet_temperature_celsius,
        ambient_temperature_celsius=ambient_temperature_celsius,
        charge_fet_enabled=charge_fet_enabled,
        discharge_fet_enabled=discharge_fet_enabled,
        alarms=alarms if alarms is not None else make_alarms(),
        bms_limits=bms_limits if bms_limits is not None else BmsLimits(200.0, 300.0, 55.2, 44.3),
        chain_aggregated_limits=chain_aggregated_limits,
    )
