"""Builders for core value objects and protocol responses with sensible healthy-pack
defaults, so each test states only what it cares about. make_pack_status() and
make_raw_status() describe the same healthy 4-cell pack, so raw-window validation passes on
the pair."""

from struct import pack

from battery_bank.core.values import (
    AlarmSeverity,
    BatterySnapshot,
    BmsLimits,
    ChainAggregatedLimits,
    PackAlarms,
    PackIdentity,
)
from battery_bank.transport.up16s import PackParams2, PackStatus
from battery_bank.transport.up16s_raw_window import RawStatus

ALARM_CATEGORY_NAMES = tuple(PackAlarms.__dataclass_fields__)


def make_pack_status(
    pack_voltage=1321,  # 13.21 V, consistent with the default cell voltages
    soc=8000,
    charge_current_limit=100,
    discharge_current_limit=2500,
    mosfet_flags=0b11,
    fault_flags=0,
    cell_voltages=(3301, 3302, 3303, 3304),
    cell_balancing_flags=0b0011,
) -> PackStatus:
    return PackStatus(
        pack_voltage=pack_voltage,
        unknown1=0,
        current=299500,
        soc=soc,
        remaining_capacity=8000,
        full_capacity=10000,
        rated_capacity=10500,
        mosfet_temp=750,
        ambient_temp=800,
        operation_status=0,
        soh=100,
        fault_flags=fault_flags,
        alarm_flags=0,
        mosfet_flags=mosfet_flags,
        connection_state_flags=0,
        charge_cycles=42,
        max_v_cell_num=4,
        max_cell_voltage=3304,
        min_v_cell_num=1,
        min_cell_voltage=3301,
        avg_cell_voltage=3302,
        max_t_sensor_num=1,
        max_cell_temp=700,
        min_t_sensor_num=2,
        min_cell_temp=695,
        avg_cell_temp=698,
        maximum_charge_voltage=552,
        charge_current_limit=charge_current_limit,
        minimum_discharge_voltage=443,
        discharge_current_limit=discharge_current_limit,
        cell_count=len(cell_voltages),
        cell_voltages=cell_voltages,
        temperatures_count=2,
        temperatures=(700, 695),
        unknown2=0,
        cell_balancing_flags=cell_balancing_flags,
        firmware_version=0x0C01,
        pack_serial_number=b"SN-1".ljust(30, b"\x00"),
    )


def make_params2(high_res_soc=8123, total_discharge=12345) -> PackParams2:
    return PackParams2(high_res_soc=high_res_soc, unused=b"\x00" * 8, total_charge=20000, total_discharge=total_discharge)


def make_raw_status_registers(address=2) -> list[int]:
    """The raw-window register image (indexed by absolute register number) of the same
    healthy pack make_pack_status() describes."""
    registers = [0] * (RawStatus.LAST_REGISTER + 1)
    registers[0x02] = 29500  # -5 A pre-deadband
    registers[0x06] = 1321
    registers[0x08] = 29500  # -5 A deadbanded
    registers[0x09] = 750
    registers[0x0A] = 800
    registers[0x0C : 0x0C + 4] = [3301, 3302, 3303, 3304]
    registers[0x1C : 0x1C + 2] = [700, 695]
    registers[0x24], registers[0x25] = 4, 3304
    registers[0x26], registers[0x27] = 1, 3301
    registers[0x28], registers[0x29] = 1, 700
    registers[0x2A], registers[0x2B] = 2, 695
    registers[0x30] = 0b11
    registers[0x31] = 8000
    registers[0x32] = 8000
    registers[0x35] = 2  # discharging, agreeing with the negative current
    registers[0x36] = 698
    registers[0x37] = 3302
    registers[0x3C], registers[0x3D] = 100, 2500
    registers[0x3E], registers[0x3F] = 552, 443
    registers[0x51] = 0b0011
    registers[0x52] = address
    return registers


def raw_status_payload(registers: list[int]) -> bytes:
    return pack(f">{RawStatus.MODBUS_ADDR_LEN}H", *registers[RawStatus.FIRST_REGISTER : RawStatus.LAST_REGISTER + 1])


def make_raw_status(address=2) -> RawStatus:
    return RawStatus.from_payload(raw_status_payload(make_raw_status_registers(address)))


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
