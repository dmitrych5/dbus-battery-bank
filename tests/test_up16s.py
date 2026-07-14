from struct import Struct, pack

import pytest

from battery_bank.core.values import AlarmSeverity
from battery_bank.transport.up16s import (
    CRC_STRUCT,
    FrameError,
    IndividualPackStatus,
    PackParams2,
    PackStatus,
    SetSoc,
    build_frame,
    build_request,
    crc16,
    decode_alarms,
    from_raw_current_with_offset_to_amps,
    from_raw_string,
    from_raw_temperature_to_celsius,
    parse_response,
)


def response_frame(address: int, command, payload: bytes) -> bytes:
    return build_frame(address, command.MODBUS_FUNC, command.MODBUS_START_ADDR, command.MODBUS_START_ADDR + command.MODBUS_ADDR_LEN, payload)


class TestCodec:
    def test_crc16_matches_the_modbus_check_value(self):
        assert crc16(b"123456789") == 0x4B37

    def test_request_layout(self):
        request = build_request(address=1, command=PackStatus)
        assert request[:8] == bytes.fromhex("01 78 10 00 10 a0 00 00".replace(" ", ""))
        assert CRC_STRUCT.unpack(request[8:])[0] == crc16(request[:8])

    def test_round_trip(self):
        payload = Struct(">96sHH").pack(b"\x00" * 96, 150, 2500)
        parsed = parse_response(1, IndividualPackStatus, response_frame(1, IndividualPackStatus, payload))
        assert parsed.charge_current_limit == 150
        assert parsed.discharge_current_limit == 2500

    def test_address_mismatch_raises(self):
        frame = response_frame(2, PackParams2, PackParams2.STRUCT.pack(9550, b"\x00" * 8, 100, 200))
        with pytest.raises(FrameError, match="address mismatch"):
            parse_response(1, PackParams2, frame)

    def test_function_code_mismatch_raises(self):
        frame = response_frame(1, PackParams2, PackParams2.STRUCT.pack(9550, b"\x00" * 8, 100, 200))
        with pytest.raises(FrameError, match="function code mismatch"):
            parse_response(1, IndividualPackStatus, frame)

    def test_corrupted_crc_raises(self):
        frame = bytearray(response_frame(1, PackParams2, PackParams2.STRUCT.pack(9550, b"\x00" * 8, 100, 200)))
        frame[10] ^= 0xFF
        with pytest.raises(FrameError, match="CRC mismatch"):
            parse_response(1, PackParams2, bytes(frame))

    def test_truncated_response_raises(self):
        frame = response_frame(1, PackParams2, PackParams2.STRUCT.pack(9550, b"\x00" * 8, 100, 200))
        with pytest.raises(FrameError, match="incomplete"):
            parse_response(1, PackParams2, frame[:-4])

    def test_payload_too_short_for_the_command_raises(self):
        with pytest.raises(FrameError, match="cannot unpack"):
            parse_response(1, PackParams2, response_frame(1, PackParams2, b"\x01\x02"))

    def test_set_soc_request_payload(self):
        assert SetSoc.request_payload(100.0) == bytes([0x11, 0x4A, 0x42, 0x44]) + pack(">H", 10000)


class TestPackStatusParsing:
    def make_payload(self, cell_voltages=(3301, 3302, 3303, 3304), temperatures=(700, 705), balancing_flags=0b0101):
        prefix_values = list(range(100, 130))  # 30 distinct, identifiable prefix fields
        payload = PackStatus.PREFIX_STRUCT.pack(*prefix_values)
        payload += pack(f">H{len(cell_voltages)}H", len(cell_voltages), *cell_voltages)
        payload += pack(f">H{len(temperatures)}H", len(temperatures), *temperatures)
        payload += PackStatus.SUFFIX_STRUCT.pack(7, balancing_flags, 0x0C01, b"SN-42".ljust(30, b"\x00"))
        return payload

    def test_variable_length_sections_parse_at_correct_offsets(self):
        status = PackStatus.from_payload(self.make_payload())
        assert status.pack_voltage == 100
        assert status.current == 102
        assert status.discharge_current_limit == 129
        assert status.cell_count == 4
        assert status.cell_voltages == (3301, 3302, 3303, 3304)
        assert status.temperatures == (700, 705)
        assert status.cell_balancing_flags == 0b0101
        assert status.firmware_version == 0x0C01
        assert from_raw_string(status.pack_serial_number) == "SN-42"

    def test_conversions(self):
        assert from_raw_temperature_to_celsius(700) == pytest.approx(20.0)
        assert from_raw_current_with_offset_to_amps(299500) == pytest.approx(-5.0)


class TestDecodeAlarms:
    def test_no_flags_is_all_ok(self):
        alarms = decode_alarms(0, 0)
        assert all(getattr(alarms, category) is AlarmSeverity.OK for category in alarms.__dataclass_fields__)

    def test_fault_bits_map_to_alarm_severity(self):
        alarms = decode_alarms(1 << 0, 0)
        assert alarms.high_cell_voltage is AlarmSeverity.ALARM

    def test_warning_bits_map_to_warning_severity(self):
        alarms = decode_alarms(0, 1 << 1)
        assert alarms.low_cell_voltage is AlarmSeverity.WARNING

    def test_fault_wins_over_warning_in_the_same_category(self):
        alarms = decode_alarms(1 << 3, 1 << 3)
        assert alarms.low_voltage is AlarmSeverity.ALARM

    def test_short_circuit_fault_maps_to_high_discharge_current(self):
        assert decode_alarms(1 << 18, 0).high_discharge_current is AlarmSeverity.ALARM

    def test_full_charge_protection_maps_to_high_voltage_warning(self):
        assert decode_alarms(1 << 25, 0).high_voltage is AlarmSeverity.WARNING

    def test_internal_failures_aggregate_from_both_flag_words(self):
        assert decode_alarms(1 << 20, 0).internal_failure is AlarmSeverity.ALARM  # temp sensor failure
        assert decode_alarms(0, 1 << 16).internal_failure is AlarmSeverity.ALARM  # EEPROM fault
