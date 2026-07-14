"""Undocumented UP16S raw status window (function 0x78, request addresses 0x3000+).

The window is a direct view of the BMS's internal status array: request start address maps to
array register (`register = start_addr - 0x3000`), the end address is exclusive, and the
firmware caps registers at WINDOW_REGISTER_COUNT. Unlike PackStatus, which a chain master
answers for its slaves from a cache refreshed only about every 50 s, this read reaches the
addressed pack's live state — the reason the command is worth validating at all.

UNTRUSTED: the command is not JBD-documented, so nothing decoded here may feed control until
it has been validated against the proven serialized commands (see docs/up16s-raw-window.md
for the register reference and the validation strategy). Two traps to keep in mind:

- Encodings deliberately differ from the serialized commands for the same quantity — pack
  current is offset by 30000 here but 300000 in PackStatus, and register 0xF6 is deci-Kelvin.
- The protection/warning bitmasks are NOT in this window; the serialized commands stay the
  only source of truth for faults.

Current use: a one-shot labeled dump at pack discovery, logged for offline validation.
"""

from dataclasses import dataclass
from struct import Struct
from typing import Callable, Sequence

from battery_bank.transport.up16s import FUNC_READ, Command, FrameError, build_frame, from_raw_temperature_to_celsius

WINDOW_START_ADDR = 0x3000
WINDOW_REGISTER_COUNT = 0x200


@dataclass(frozen=True)
class RawWindow(Command):
    """A contiguous run of raw 16-bit status-array registers; the payload length alone
    determines how many arrive."""

    MODBUS_FUNC = FUNC_READ

    registers: tuple[int, ...]

    @classmethod
    def from_payload(cls, payload: bytes) -> "RawWindow":
        if len(payload) % 2:
            raise FrameError(f"raw window payload of {len(payload)} bytes is not a whole number of registers")
        return cls(Struct(f">{len(payload) // 2}H").unpack(payload))


def build_window_request(address: int, first_register: int, register_count: int) -> bytes:
    start_addr = WINDOW_START_ADDR + first_register
    return build_frame(address, FUNC_READ, start_addr, start_addr + register_count)


def from_raw_window_current_to_amps(raw: int) -> float:
    """The window's current offset is 30000, not PackStatus's 300000 — same quantity,
    different serializer."""
    return (raw - 30000) / 100


@dataclass(frozen=True)
class _Field:
    label: str
    decode: Callable[[int], str]
    register_count: int = 1  # 32-bit fields span two registers, high word first


def _scaled(divisor: int, decimals: int, unit: str) -> Callable[[int], str]:
    return lambda raw: f"{raw / divisor:.{decimals}f} {unit}"


_pack_volts = _scaled(100, 2, "V")
_limit_volts = _scaled(10, 1, "V")
_limit_amps = _scaled(10, 1, "A")
_capacity_ah = _scaled(100, 2, "Ah")
_throughput_ah = _scaled(10, 1, "Ah")
_fine_percent = _scaled(100, 2, "%")


def _millivolts(raw: int) -> str:
    return f"{raw} mV"


def _temperature(raw: int) -> str:
    return f"{from_raw_temperature_to_celsius(raw):.1f} °C"


def _window_current(raw: int) -> str:
    return f"{from_raw_window_current_to_amps(raw):.2f} A"


def _percent(raw: int) -> str:
    return f"{raw} %"


def _plain(raw: int) -> str:
    return str(raw)


def _hex(raw: int) -> str:
    return f"0x{raw:04X}"


def _bitmask(raw: int) -> str:
    return f"0b{raw:016b}"


def _mos_state(raw: int) -> str:
    return f"charge {'on' if raw & 1 else 'off'}, discharge {'on' if raw & 2 else 'off'}"


_OPERATION_STATUSES = {0: "idle", 1: "charging", 2: "discharging"}


def _operation_status(raw: int) -> str:
    return _OPERATION_STATUSES.get(raw, f"unknown ({raw})")


def _signed_total_current(raw: int) -> str:
    if raw >= 1 << 31:
        raw -= 1 << 32
    return f"{raw / 100:.2f} A (scale unverified)"


def _deci_kelvin(raw: int) -> str:
    return f"{(raw - 0x0AAB) / 10:.1f} °C"


def _build_fields() -> dict[int, _Field]:
    fields = {
        0x06: _Field("pack voltage", _pack_volts),
        0x08: _Field("pack current", _window_current),
        0x09: _Field("MOSFET temperature", _temperature),
        0x0A: _Field("ambient temperature", _temperature),
        0x24: _Field("max-voltage cell number", _plain),
        0x25: _Field("max cell voltage", _millivolts),
        0x26: _Field("min-voltage cell number", _plain),
        0x27: _Field("min cell voltage", _millivolts),
        0x28: _Field("max-temperature sensor number", _plain),
        0x29: _Field("max cell temperature", _temperature),
        0x2A: _Field("min-temperature sensor number", _plain),
        0x2B: _Field("min cell temperature", _temperature),
        0x30: _Field("MOS state", _mos_state),
        0x31: _Field("SOC (high-resolution)", _fine_percent),
        0x32: _Field("residual capacity", _capacity_ah),
        0x35: _Field("operation status", _operation_status),
        0x36: _Field("average cell temperature", _temperature),
        0x37: _Field("average cell voltage", _millivolts),
        0x38: _Field("cell spread max-min (unconfirmed)", _millivolts),
        0x3A: _Field("cell fault/offline bitmask (unconfirmed)", _bitmask),
        0x3B: _Field("temperature-sensor fault bitmask (unconfirmed)", _bitmask),
        0x3C: _Field("charge current limit", _limit_amps),
        0x3D: _Field("discharge current limit", _limit_amps),
        0x3E: _Field("charge voltage limit", _limit_volts),
        0x3F: _Field("discharge voltage limit", _limit_volts),
        0x41: _Field("total charged (unconfirmed)", _throughput_ah, register_count=2),
        0x43: _Field("total discharged (unconfirmed)", _throughput_ah, register_count=2),
        0x46: _Field("debug-mode state", _hex),
        0x49: _Field("ADC-derived hardware id", _hex),
        0x4B: _Field("tooling MOS override", _hex),
        0x4C: _Field("tooling contact override", _hex),
        0x4D: _Field("sleep command", _hex),
        0x4E: _Field("tooling balance override", _hex),
        0x51: _Field("balancing bitmask", _bitmask),
        0x52: _Field("battery address", _plain),
        0x55: _Field("MOS enable/disable control", _hex),
        0x58: _Field("comm watchdog", _hex),
        0x59: _Field("comm watchdog", _hex),
        0x5A: _Field("shutdown request", _hex),
        # 0xC8+ block: the whole-bank rollup on the master, the pack itself on a forwarded slave.
        0xC8: _Field("aggregated pack voltage", _pack_volts),
        0xC9: _Field("aggregated total current", _signed_total_current, register_count=2),
        0xD0: _Field("SOC (coarse)", _percent),
        0xD1: _Field("aggregated charge current limit", _limit_amps),
        0xD2: _Field("aggregated discharge current limit", _limit_amps),
        0xD3: _Field("aggregated charge voltage limit", _limit_volts),
        0xD4: _Field("aggregated discharge voltage limit", _limit_volts),
        0xD5: _Field("packs-available bitmask", _bitmask),
        0xDA: _Field("max cell voltage (aggregated)", _millivolts),
        0xDC: _Field("min cell voltage (aggregated)", _millivolts),
        0xDE: _Field("max cell temperature (aggregated)", _temperature),
        0xE0: _Field("min cell temperature (aggregated)", _temperature),
        0xE1: _Field("SOH", _percent),
        0xF3: _Field("cycle count", _plain),
        0xF6: _Field("pack temperature (deci-Kelvin)", _deci_kelvin),
    }
    for cell in range(16):
        fields[0x0C + cell] = _Field(f"cell {cell + 1} voltage", _millivolts)
    for sensor in range(4):
        fields[0x1C + sensor] = _Field(f"cell temperature sensor {sensor + 1}", _temperature)
    return fields


_FIELDS = _build_fields()
_LABEL_WIDTH = max(len(field.label) for field in _FIELDS.values()) + 1
_UNKNOWN_PER_ROW = 16


def describe_window(registers: Sequence[int]) -> str:
    """Renders a labeled multi-line dump: known registers decoded to engineering units (raw
    hex kept alongside for cross-checking), unknown ones as compact hex rows. Accepts a
    partial window — a field truncated by the end of the data falls back to hex."""
    lines: list[str] = []
    unknown: list[tuple[int, int]] = []

    def flush_unknown() -> None:
        for row_start in range(0, len(unknown), _UNKNOWN_PER_ROW):
            row = unknown[row_start : row_start + _UNKNOWN_PER_ROW]
            lines.append(f"0x{row[0][0]:03X}: " + " ".join(f"{value:04X}" for _, value in row))
        unknown.clear()

    register = 0
    while register < len(registers):
        field = _FIELDS.get(register)
        if field is None or register + field.register_count > len(registers):
            unknown.append((register, registers[register]))
            register += 1
            continue
        flush_unknown()
        raw = 0
        for offset in range(field.register_count):
            raw = (raw << 16) | registers[register + offset]
        line_label = f"{field.label}:"
        lines.append(f"0x{register:03X} {line_label:<{_LABEL_WIDTH}} {field.decode(raw)}  (raw 0x{raw:0{4 * field.register_count}X})")
        register += field.register_count
    flush_unknown()
    return "\n".join(lines)
