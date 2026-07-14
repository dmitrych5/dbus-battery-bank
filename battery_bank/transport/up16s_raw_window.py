"""Undocumented UP16S raw status window (function 0x78 at 0x3000+).

A direct window onto the BMS's internal live-data array, documented (from firmware reverse
engineering) in ../up16s_protocol_docs/ecoworthy.md under "Raw status registers". Two things
make an undocumented command worth using:

- it bypasses the chain master's ~50 s cache of slave state, so daisy-chained slaves are
  read in real time — the only known way to get fresh slave data every cycle;
- the pre-deadband pack current (register 0x02) exists only here.

Because the command is undocumented it may never be trusted blindly: validate() checks every
response against its own internal invariants and cross-references it against the proven
PackStatus from the same cycle, and the poller disables the command for a battery on the
first failure (until restart), falling back to the documented commands. The BMS
protection/warning bitmasks are NOT in this window, so PackStatus remains the source of
truth for alarms regardless.

Only the per-pack live block (registers 0x02-0x52) is read. The aggregated bank block at
0xC8+ is deliberately not requested — all packs are polled individually in this process.
Encoding traps: the current offset is 30000 (not PackStatus's 300000), and the aggregated
block's temperatures (unused here) are deci-Kelvin rather than Celsius-offset.
"""

from dataclasses import dataclass
from struct import Struct
from typing import Callable, ClassVar, Sequence

from battery_bank.transport import up16s

WINDOW_START_ADDR = 0x3000

OPERATION_STATUS_CHARGING = 1
OPERATION_STATUS_DISCHARGING = 2

MOSFET_STATE_CHARGE_ON = 1
MOSFET_STATE_DISCHARGE_ON = 2
"""NB: the opposite bit order to PackStatus.mosfet_flags."""


@dataclass(frozen=True)
class RawStatus(up16s.Command):
    """The per-pack live block of the raw status window. Fields keep the documented register
    layout's 16 cell and 4 sensor slots; slice by PackStatus's counts before use."""

    MODBUS_FUNC = up16s.FUNC_READ
    FIRST_REGISTER: ClassVar[int] = 0x02
    LAST_REGISTER: ClassVar[int] = 0x52
    """The battery-address register: read not for its content but as a validation anchor."""
    MODBUS_START_ADDR = WINDOW_START_ADDR + FIRST_REGISTER
    MODBUS_ADDR_LEN = LAST_REGISTER - FIRST_REGISTER + 1

    STRUCT: ClassVar[Struct] = Struct(f">{MODBUS_ADDR_LEN}H")

    raw_current: int  # pre-deadband, unlike `current`; offset 30000, unlike PackStatus
    pack_voltage: int
    current: int  # deadbanded like PackStatus.current, but offset 30000
    mosfet_temp: int
    ambient_temp: int
    cell_voltages: tuple[int, ...]
    temperatures: tuple[int, ...]
    max_v_cell_num: int
    max_cell_voltage: int
    min_v_cell_num: int
    min_cell_voltage: int
    max_t_sensor_num: int
    max_cell_temp: int
    min_t_sensor_num: int
    min_cell_temp: int
    mosfet_state: int  # MOSFET_STATE_* bits
    soc: int  # 0.01 % units, same resolution as PackParams2.high_res_soc
    residual_capacity: int
    operation_status: int  # OPERATION_STATUS_*; 0 is idle
    avg_cell_temp: int
    avg_cell_voltage: int
    charge_current_limit: int  # non-aggregated even on a chain master, unlike PackStatus
    discharge_current_limit: int
    maximum_charge_voltage: int
    minimum_discharge_voltage: int
    cell_balancing_flags: int
    address: int

    @classmethod
    def from_payload(cls, payload: bytes) -> "RawStatus":
        registers = cls.STRUCT.unpack_from(payload)

        def register(index: int) -> int:
            return registers[index - cls.FIRST_REGISTER]

        def register_run(first: int, count: int) -> tuple[int, ...]:
            return registers[first - cls.FIRST_REGISTER : first - cls.FIRST_REGISTER + count]

        return cls(
            raw_current=register(0x02),
            pack_voltage=register(0x06),
            current=register(0x08),
            mosfet_temp=register(0x09),
            ambient_temp=register(0x0A),
            cell_voltages=register_run(0x0C, 16),
            temperatures=register_run(0x1C, 4),
            max_v_cell_num=register(0x24),
            max_cell_voltage=register(0x25),
            min_v_cell_num=register(0x26),
            min_cell_voltage=register(0x27),
            max_t_sensor_num=register(0x28),
            max_cell_temp=register(0x29),
            min_t_sensor_num=register(0x2A),
            min_cell_temp=register(0x2B),
            mosfet_state=register(0x30),
            soc=register(0x31),
            residual_capacity=register(0x32),
            operation_status=register(0x35),
            avg_cell_temp=register(0x36),
            avg_cell_voltage=register(0x37),
            charge_current_limit=register(0x3C),
            discharge_current_limit=register(0x3D),
            maximum_charge_voltage=register(0x3E),
            minimum_discharge_voltage=register(0x3F),
            cell_balancing_flags=register(0x51),
            address=register(0x52),
        )


def from_raw_window_current_to_amps(raw: int) -> float:
    """The window's current offset is 30000, not PackStatus's 300000 — same quantity,
    different serializer."""
    return (raw - 30000) / 100


# Validation tolerances. Internal checks compare values within one response; cross-checks
# compare against PackStatus, whose data for a chain slave may be up to ~50 s stale — every
# cross tolerance must absorb that drift plus a worst-case load step, because a single false
# positive disables the command until restart. A broken decode is off by far more than any
# of these.
CELL_SANE_MILLIVOLTS = (1500.0, 4500.0)
TEMPERATURE_SANE_CELSIUS = (-40.0, 120.0)
LIMIT_CURRENT_SANE_AMPS = (0.0, 600.0)
LIMIT_VOLTAGE_SANE_VOLTS = (10.0, 100.0)
SOC_SANE_PERCENT = (0.0, 100.0)
EXTREME_TOLERANCE_MILLIVOLTS = 25.0
EXTREME_TOLERANCE_CELSIUS = 1.0
AVERAGE_TOLERANCE_MILLIVOLTS = 25.0
AVERAGE_TOLERANCE_CELSIUS = 1.0
CELL_SUM_TOLERANCE_VOLTS = 2.0
DEADBAND_TOLERANCE_AMPS = 5.0
OPERATION_SIGN_TOLERANCE_AMPS = 5.0
CROSS_VOLTAGE_TOLERANCE_VOLTS = 4.0
CROSS_CELL_TOLERANCE_MILLIVOLTS = 500.0
CROSS_SOC_TOLERANCE_PERCENT = 5.0
CROSS_TEMPERATURE_TOLERANCE_CELSIUS = 10.0
CROSS_MOSFET_TEMPERATURE_TOLERANCE_CELSIUS = 20.0
CROSS_CAPACITY_TOLERANCE_AH = 15.0


def validate(raw: RawStatus, pack_status: up16s.PackStatus, address: int) -> list[str]:
    """Checks a RawStatus response against its own internal invariants and cross-references
    it against the same cycle's PackStatus, comparing in physical units (the encodings
    differ between the two commands). Returns one human-readable description per failed
    check, naming the exact values and their sources; an empty list means the response is
    trustworthy."""
    to_celsius = up16s.from_raw_temperature_to_celsius
    to_volts = up16s.from_raw_pack_voltage_to_volts
    to_percent = up16s.from_raw_high_resolution_percentage
    to_ah = up16s.from_raw_capacity_to_ah
    cells_mv = raw.cell_voltages[: pack_status.cell_count]
    temperatures_c = [to_celsius(value) for value in raw.temperatures[: pack_status.temperatures_count]]
    deadbanded_amps = from_raw_window_current_to_amps(raw.current)
    failures: list[str] = []

    def expect(condition: bool, description: str) -> None:
        if not condition:
            failures.append(description)

    def expect_close(quantity: str, window_value: float, reference: float, reference_source: str, tolerance: float, unit: str) -> None:
        expect(
            abs(window_value - reference) <= tolerance,
            f"{quantity}: raw window has {window_value:g} {unit}, {reference_source} has {reference:g} {unit} (tolerance {tolerance:g} {unit})",
        )

    def expect_sane(quantity: str, value: float, sane: tuple[float, float], unit: str) -> None:
        low, high = sane
        expect(low <= value <= high, f"{quantity}: raw window value {value:g} {unit} is outside the sane range {low:g}..{high:g} {unit}")

    def expect_extreme(
        quantity: str, number: int, claimed: float, values: Sequence[float], pick: Callable[[Sequence[float]], float], tolerance: float, unit: str
    ) -> None:
        expect(
            abs(claimed - pick(values)) <= tolerance,
            f"{quantity}: raw window claims {claimed:g} {unit} but its own decoded values give {pick(values):g} {unit} (tolerance {tolerance:g} {unit})",
        )
        if not 1 <= number <= len(values):
            failures.append(f"{quantity}: locator points at #{number}, outside 1..{len(values)}")
        else:
            expect(
                abs(values[number - 1] - claimed) <= tolerance,
                f"{quantity}: locator points at #{number} holding {values[number - 1]:g} {unit}, claimed {claimed:g} {unit} (tolerance {tolerance:g} {unit})",
            )

    expect(raw.address == address, f"battery address: raw window register 0x52 says {raw.address}, the request was addressed to {address}")

    # Internal invariants — a wrong register offset breaks these immediately.
    for cell_number, millivolts in enumerate(cells_mv, start=1):
        expect_sane(f"cell {cell_number} voltage", millivolts, CELL_SANE_MILLIVOLTS, "mV")
    for sensor_number, celsius in enumerate(temperatures_c, start=1):
        expect_sane(f"cell temperature {sensor_number}", celsius, TEMPERATURE_SANE_CELSIUS, "°C")
    expect_sane("MOSFET temperature", to_celsius(raw.mosfet_temp), TEMPERATURE_SANE_CELSIUS, "°C")
    expect_sane("ambient temperature", to_celsius(raw.ambient_temp), TEMPERATURE_SANE_CELSIUS, "°C")
    expect_sane("SoC", to_percent(raw.soc), SOC_SANE_PERCENT, "%")
    expect_sane("charge current limit", up16s.from_raw_current_to_amps(raw.charge_current_limit), LIMIT_CURRENT_SANE_AMPS, "A")
    expect_sane("discharge current limit", up16s.from_raw_current_to_amps(raw.discharge_current_limit), LIMIT_CURRENT_SANE_AMPS, "A")
    expect_sane("charge voltage limit", up16s.from_raw_dvcc_voltage_to_volts(raw.maximum_charge_voltage), LIMIT_VOLTAGE_SANE_VOLTS, "V")
    expect_sane("discharge voltage limit", up16s.from_raw_dvcc_voltage_to_volts(raw.minimum_discharge_voltage), LIMIT_VOLTAGE_SANE_VOLTS, "V")

    expect_extreme("max cell voltage", raw.max_v_cell_num, raw.max_cell_voltage, cells_mv, max, EXTREME_TOLERANCE_MILLIVOLTS, "mV")
    expect_extreme("min cell voltage", raw.min_v_cell_num, raw.min_cell_voltage, cells_mv, min, EXTREME_TOLERANCE_MILLIVOLTS, "mV")
    expect_extreme("max cell temperature", raw.max_t_sensor_num, to_celsius(raw.max_cell_temp), temperatures_c, max, EXTREME_TOLERANCE_CELSIUS, "°C")
    expect_extreme("min cell temperature", raw.min_t_sensor_num, to_celsius(raw.min_cell_temp), temperatures_c, min, EXTREME_TOLERANCE_CELSIUS, "°C")
    expect_close("average cell voltage", raw.avg_cell_voltage, sum(cells_mv) / len(cells_mv), "its own decoded cell average", AVERAGE_TOLERANCE_MILLIVOLTS, "mV")
    expect_close(
        "average cell temperature", to_celsius(raw.avg_cell_temp), sum(temperatures_c) / len(temperatures_c), "its own decoded sensor average", AVERAGE_TOLERANCE_CELSIUS, "°C"
    )
    expect_close("pack voltage", to_volts(raw.pack_voltage), sum(cells_mv) / 1000, "its own decoded cell sum", CELL_SUM_TOLERANCE_VOLTS, "V")
    expect_close(
        "pre-deadband current (register 0x02)",
        from_raw_window_current_to_amps(raw.raw_current),
        deadbanded_amps,
        "the deadbanded current (register 0x08)",
        DEADBAND_TOLERANCE_AMPS,
        "A",
    )
    expect(
        raw.cell_balancing_flags >> pack_status.cell_count == 0,
        f"balancing bitmask: raw window value 0b{raw.cell_balancing_flags:b} has bits set beyond the {pack_status.cell_count} cells",
    )
    if raw.operation_status == OPERATION_STATUS_CHARGING:
        expect(deadbanded_amps > -OPERATION_SIGN_TOLERANCE_AMPS, f"operation status: raw window says charging while its deadbanded current is {deadbanded_amps:g} A")
    elif raw.operation_status == OPERATION_STATUS_DISCHARGING:
        expect(deadbanded_amps < OPERATION_SIGN_TOLERANCE_AMPS, f"operation status: raw window says discharging while its deadbanded current is {deadbanded_amps:g} A")

    # Cross-checks against PackStatus — only quantities whose drift over the master's ~50 s
    # slave cache is physically bounded. Current, limits, and FET/balancing/operation state
    # can change legitimately faster than the cache refreshes and are deliberately not
    # cross-checked; their range checks above still catch structural garbage.
    expect_close("pack voltage", to_volts(raw.pack_voltage), to_volts(pack_status.pack_voltage), "PackStatus", CROSS_VOLTAGE_TOLERANCE_VOLTS, "V")
    expect_close("SoC", to_percent(raw.soc), to_percent(pack_status.soc), "PackStatus", CROSS_SOC_TOLERANCE_PERCENT, "%")
    expect_close("remaining capacity", to_ah(raw.residual_capacity), to_ah(pack_status.remaining_capacity), "PackStatus", CROSS_CAPACITY_TOLERANCE_AH, "Ah")
    for cell_number, (window_mv, status_mv) in enumerate(zip(cells_mv, pack_status.cell_voltages), start=1):
        expect_close(f"cell {cell_number} voltage", window_mv, status_mv, "PackStatus", CROSS_CELL_TOLERANCE_MILLIVOLTS, "mV")
    for sensor_number, (window_c, status_raw) in enumerate(zip(temperatures_c, pack_status.temperatures), start=1):
        expect_close(f"cell temperature {sensor_number}", window_c, to_celsius(status_raw), "PackStatus", CROSS_TEMPERATURE_TOLERANCE_CELSIUS, "°C")
    expect_close("MOSFET temperature", to_celsius(raw.mosfet_temp), to_celsius(pack_status.mosfet_temp), "PackStatus", CROSS_MOSFET_TEMPERATURE_TOLERANCE_CELSIUS, "°C")
    expect_close("ambient temperature", to_celsius(raw.ambient_temp), to_celsius(pack_status.ambient_temp), "PackStatus", CROSS_TEMPERATURE_TOLERANCE_CELSIUS, "°C")
    return failures
