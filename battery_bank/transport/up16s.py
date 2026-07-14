"""JBD UP16S protocol codec: request building, response frame validation, command payload
parsing, and raw-value conversion. Pure — serial I/O and retry policy live in the acquisition
layer, which catches FrameError and feeds command availability tracking.

Protocol reference: https://gist.github.com/PhracturedBlue/7ef619594eaa4c27f4ff068b461865b8
Tested on JBD UP16S015 with firmware 10.2.1 and 12.1.7. Command layouts are ported verbatim
from the field-proven driver in ../venus-os_dbus-serialbattery/dbus-serialbattery/bms/lltjbd_up16s.py.
"""

from dataclasses import dataclass
from struct import Struct, error as StructError
from typing import ClassVar, TypeVar

from battery_bank.core.values import AlarmSeverity, PackAlarms

FUNC_INDIVIDUAL_PACK_STATUS = 0x45
FUNC_READ = 0x78
FUNC_WRITE = 0x79

FRAME_HEADER_STRUCT = Struct(">BBHHH")  # address, function code, start addr, end addr, payload length
CRC_STRUCT = Struct("<H")  # unlike everything else in the protocol, the CRC is little-endian
BIG_ENDIAN_SHORT_STRUCT = Struct(">H")
RESPONSE_PAYLOAD_LENGTH_OFFSET = 6

JBD_WRITE_PAYLOAD_PREFIX = bytes([0x11, 0x4A, 0x42, 0x44])

CommandT = TypeVar("CommandT", bound="Command")


class FrameError(Exception):
    """A response frame failed validation or parsing; the acquisition layer decides whether
    this means retry, interference, or command unavailability."""


class Command:
    MODBUS_FUNC: ClassVar[int]
    MODBUS_START_ADDR: ClassVar[int]
    MODBUS_ADDR_LEN: ClassVar[int]
    STRUCT: ClassVar[Struct]

    @classmethod
    def from_payload(cls: type[CommandT], payload: bytes) -> CommandT:
        return cls(*cls.STRUCT.unpack_from(payload))


@dataclass(frozen=True)
class PackStatus(Command):
    """Live status of one pack. The only command every pack answers on every port; a chain
    master answers it with chain-aggregated CCL/DCL/CVL/DVL instead of its own."""

    MODBUS_FUNC = FUNC_READ
    MODBUS_START_ADDR = 0x1000
    MODBUS_ADDR_LEN = 0xA0

    PREFIX_STRUCT: ClassVar[Struct] = Struct(">HHIHHHHHHHHIIHHHHHHHHHHHHHHHHH")  # fields before cell_count
    SUFFIX_STRUCT: ClassVar[Struct] = Struct(">HHH30s")  # fields after temperatures

    pack_voltage: int
    unknown1: int
    current: int
    soc: int  # in 0.01% units, but the master only knows whole percents for slaves
    remaining_capacity: int
    full_capacity: int
    rated_capacity: int
    mosfet_temp: int
    ambient_temp: int
    operation_status: int
    soh: int
    fault_flags: int
    alarm_flags: int
    mosfet_flags: int
    connection_state_flags: int
    charge_cycles: int
    max_v_cell_num: int
    max_cell_voltage: int
    min_v_cell_num: int
    min_cell_voltage: int
    avg_cell_voltage: int
    max_t_sensor_num: int
    max_cell_temp: int
    min_t_sensor_num: int
    min_cell_temp: int
    avg_cell_temp: int
    maximum_charge_voltage: int
    charge_current_limit: int
    minimum_discharge_voltage: int
    discharge_current_limit: int
    cell_count: int
    cell_voltages: tuple[int, ...]
    temperatures_count: int
    temperatures: tuple[int, ...]
    unknown2: int
    cell_balancing_flags: int  # bitmask of which cells are balancing, cell 1 at the least significant bit
    firmware_version: int  # not available when the master forwards a slave's status
    pack_serial_number: bytes
    # The response continues with fields this project does not need.

    MOSFET_FLAG_DISCHARGE_ENABLED: ClassVar[int] = 1
    MOSFET_FLAG_CHARGE_ENABLED: ClassVar[int] = 2

    @classmethod
    def from_payload(cls, payload: bytes) -> "PackStatus":
        offset = 0
        prefix = cls.PREFIX_STRUCT.unpack_from(payload, offset)
        offset += cls.PREFIX_STRUCT.size

        cell_count = BIG_ENDIAN_SHORT_STRUCT.unpack_from(payload, offset)[0]
        offset += BIG_ENDIAN_SHORT_STRUCT.size
        cell_voltages = Struct(f">{cell_count}H").unpack_from(payload, offset)
        offset += BIG_ENDIAN_SHORT_STRUCT.size * cell_count

        temperatures_count = BIG_ENDIAN_SHORT_STRUCT.unpack_from(payload, offset)[0]
        offset += BIG_ENDIAN_SHORT_STRUCT.size
        temperatures = Struct(f">{temperatures_count}H").unpack_from(payload, offset)
        offset += BIG_ENDIAN_SHORT_STRUCT.size * temperatures_count

        suffix = cls.SUFFIX_STRUCT.unpack_from(payload, offset)
        return cls(*prefix, cell_count, tuple(cell_voltages), temperatures_count, tuple(temperatures), *suffix)


@dataclass(frozen=True)
class IndividualPackStatus(Command):
    """The master's own non-aggregated CCL/DCL; only answered on a direct connection."""

    MODBUS_FUNC = FUNC_INDIVIDUAL_PACK_STATUS
    MODBUS_START_ADDR = 0x0000
    MODBUS_ADDR_LEN = 0x54

    STRUCT = Struct(">96sHH")

    unused: bytes  # most fields duplicate PackStatus
    charge_current_limit: int
    discharge_current_limit: int


@dataclass(frozen=True)
class PackParams1(Command):
    """Identity parameters. Reading from the common start address (rather than only the fields
    needed) keeps compatibility with all firmware versions."""

    MODBUS_FUNC = FUNC_READ
    MODBUS_START_ADDR = 0x1C00
    MODBUS_ADDR_LEN = 0xA0

    STRUCT = Struct(">16s30sHHH30sHHH")

    unused: bytes
    bms_model_and_sn: bytes
    bms_year: int
    bms_month: int
    bms_day: int
    pack_serial_number: bytes
    pack_year: int
    pack_month: int
    pack_day: int


@dataclass(frozen=True)
class PackParams2(Command):
    """High-resolution SoC and lifetime throughput. Deliberately a partial read starting at
    0x2006: reading from 0x2000 races with the BMS's MOSFET-state process over the hardware
    short-circuit-protection registers and occasionally resets DCL to 0 across all requests.
    Partial reads require firmware ~v12+; older firmware ignores them, which the availability
    tracking absorbs."""

    STRUCT = Struct(">H8sII")

    MODBUS_FUNC = FUNC_READ
    MODBUS_START_ADDR = 0x2006
    MODBUS_ADDR_LEN = STRUCT.size

    high_res_soc: int  # always the actual high-resolution SoC, unlike PackStatus.soc
    unused: bytes
    total_charge: int
    total_discharge: int


@dataclass(frozen=True)
class ProductInformation(Command):
    MODBUS_FUNC = FUNC_READ
    MODBUS_START_ADDR = 0x2810
    MODBUS_ADDR_LEN = 0x2C

    STRUCT = Struct(">HHHHHH16s16s")

    maybe_model_id: int
    maybe_hardware_revision: int
    firmware_major_version: int
    firmware_minor_version: int
    firmware_patch_version: int
    unknown1: int
    model: bytes
    project_code: bytes


@dataclass(frozen=True)
class SetSoc(Command):
    """Writes SoC. Availability is independent of PackParams2's: this is a plain write that
    older firmware accepts even where PackParams2's partial-read format is ignored. The BMS
    answers with an empty payload."""

    MODBUS_FUNC = FUNC_WRITE
    MODBUS_START_ADDR = 0x2006
    MODBUS_ADDR_LEN = BIG_ENDIAN_SHORT_STRUCT.size

    STRUCT = Struct("")

    @staticmethod
    def request_payload(soc_percent: float) -> bytes:
        return JBD_WRITE_PAYLOAD_PREFIX + BIG_ENDIAN_SHORT_STRUCT.pack(to_raw_high_resolution_percentage(soc_percent))


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 0x0001 else crc >> 1
    return crc


def build_request(address: int, command: type[Command], payload: bytes = b"") -> bytes:
    start = command.MODBUS_START_ADDR
    return build_frame(address, command.MODBUS_FUNC, start, start + command.MODBUS_ADDR_LEN, payload)


def build_frame(address: int, function: int, start_addr: int, end_addr: int, payload: bytes = b"") -> bytes:
    frame = FRAME_HEADER_STRUCT.pack(address, function, start_addr, end_addr, len(payload))
    frame += payload
    return frame + CRC_STRUCT.pack(crc16(frame))


def parse_response(address: int, command: type[CommandT], response: bytes) -> CommandT:
    """Validates the frame (address, function code, length, CRC) and parses the payload.
    Raises FrameError with the reason on any mismatch."""
    if len(response) < FRAME_HEADER_STRUCT.size:
        raise FrameError(f"response too short for a header: {len(response)} bytes")
    response_address, function_code, _, _, payload_length = FRAME_HEADER_STRUCT.unpack_from(response)
    if response_address != address:
        raise FrameError(f"address mismatch: expected {address}, got {response_address}")
    if function_code != command.MODBUS_FUNC:
        raise FrameError(f"function code mismatch: expected 0x{command.MODBUS_FUNC:02x}, got 0x{function_code:02x}")

    payload_end = FRAME_HEADER_STRUCT.size + payload_length
    if len(response) < payload_end + CRC_STRUCT.size:
        raise FrameError(f"response incomplete: expected {payload_end + CRC_STRUCT.size}, got {len(response)} bytes")
    crc_received = CRC_STRUCT.unpack_from(response, payload_end)[0]
    crc_calculated = crc16(response[:payload_end])
    if crc_received != crc_calculated:
        raise FrameError(f"CRC mismatch: expected 0x{crc_calculated:04X}, got 0x{crc_received:04X}")

    try:
        return command.from_payload(response[FRAME_HEADER_STRUCT.size:payload_end])
    except StructError as error:
        raise FrameError(f"cannot unpack {command.__name__} payload: {error}") from error


def decode_alarms(fault_flags: int, alarm_flags: int) -> PackAlarms:
    """Maps the BMS fault (protection acted) and alarm (warning) bitmasks onto the Victron
    alarm categories. Faults map to ALARM severity, warning bits to WARNING."""
    fault = _bits(fault_flags)
    warning = _bits(alarm_flags)
    # Bits 25 (fault word) and 18 (alarm word) are full-charge protection; both shown as
    # high-voltage warnings. Temperature-difference problems surface as cell imbalance since
    # Victron has no closer category.
    return PackAlarms(
        high_cell_voltage=_severity(fault(0), warning(0)),
        low_cell_voltage=_severity(fault(1), warning(1)),
        high_voltage=_severity(fault(2), warning(2) or fault(25) or warning(18)),
        low_voltage=_severity(fault(3), warning(3)),
        high_charge_current=_severity(fault(4) or fault(5), warning(4)),
        high_discharge_current=_severity(fault(6) or fault(7) or fault(18) or fault(27), warning(5)),
        high_charge_temperature=_severity(fault(8), warning(6)),
        low_charge_temperature=_severity(fault(9), warning(7)),
        high_temperature=_severity(fault(10) or fault(13), warning(8) or warning(11)),
        low_temperature=_severity(fault(11) or fault(14), warning(9) or warning(12)),
        high_internal_temperature=_severity(fault(12), warning(10)),
        cell_imbalance=_severity(fault(15) or fault(16), warning(13) or warning(14)),
        low_soc=_severity(fault(17), warning(15)),
        internal_failure=_severity(
            fault(19) or fault(20) or fault(21) or fault(22) or fault(23) or fault(24) or fault(26) or warning(16) or warning(17),
            False,
        ),
    )


def _bits(flags: int):
    return lambda bit: bool(flags & (1 << bit))


def _severity(is_alarm: bool, is_warning: bool) -> AlarmSeverity:
    if is_alarm:
        return AlarmSeverity.ALARM
    if is_warning:
        return AlarmSeverity.WARNING
    return AlarmSeverity.OK


# Raw value conversions. Temperatures are offset by 500 in 0.1 C units; the pack current is
# offset by 300000 in 0.01 A units.


def from_raw_temperature_to_celsius(raw: int) -> float:
    return (raw - 500) / 10


def from_raw_current_with_offset_to_amps(raw: int) -> float:
    return (raw - 300000) / 100


def from_raw_current_to_amps(raw: int) -> float:
    return raw / 10


def from_raw_dvcc_voltage_to_volts(raw: int) -> float:
    return raw / 10


def from_raw_pack_voltage_to_volts(raw: int) -> float:
    return raw / 100


def from_raw_cell_voltage_to_volts(raw: int) -> float:
    return raw / 1000


def from_raw_capacity_to_ah(raw: int) -> float:
    return raw / 100


def from_raw_total_charge_discharge_to_ah(raw: int) -> float:
    return raw / 10


def from_raw_high_resolution_percentage(raw: int) -> float:
    return raw / 100


def to_raw_high_resolution_percentage(percent: float) -> int:
    return round(percent * 100)


def from_raw_string(raw: bytes) -> str:
    return raw.rstrip(b"\x00").decode("ascii", errors="ignore")
