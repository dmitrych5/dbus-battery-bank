from struct import pack

import pytest

from battery_bank.transport.up16s import CRC_STRUCT, FUNC_READ, FrameError, build_frame, crc16, parse_response
from battery_bank.transport.up16s_raw_window import (
    WINDOW_REGISTER_COUNT,
    WINDOW_START_ADDR,
    RawWindow,
    build_window_request,
    describe_window,
    from_raw_window_current_to_amps,
)


def window_response(address: int, first_register: int, values) -> bytes:
    start = WINDOW_START_ADDR + first_register
    return build_frame(address, FUNC_READ, start, start + len(values), pack(f">{len(values)}H", *values))


class TestCodec:
    def test_request_matches_the_documented_capture(self):
        request = build_window_request(2, 0x06, 0x20 - 0x06)  # registers 6..0x1F from slave 2
        assert request[:8] == bytes.fromhex("02 78 30 06 30 20 00 00".replace(" ", ""))
        assert CRC_STRUCT.unpack(request[8:])[0] == crc16(request[:8])

    def test_round_trip(self):
        frame = window_response(1, 0, (0, 1, 0xFFFF))
        assert parse_response(1, RawWindow, frame).registers == (0, 1, 0xFFFF)

    def test_odd_payload_length_raises(self):
        frame = build_frame(1, FUNC_READ, WINDOW_START_ADDR, WINDOW_START_ADDR + 1, b"\x01")
        with pytest.raises(FrameError, match="whole number of registers"):
            parse_response(1, RawWindow, frame)

    def test_window_current_conversion(self):
        assert from_raw_window_current_to_amps(29500) == pytest.approx(-5.0)


class TestDescribeWindow:
    def lines_by_register(self, registers) -> dict[str, str]:
        return {line.split()[0]: line for line in describe_window(registers).splitlines()}

    def test_known_registers_are_decoded_and_labeled(self):
        registers = [0] * WINDOW_REGISTER_COUNT
        registers[0x06] = 5328
        registers[0x08] = 29500
        registers[0x0C] = 3301
        registers[0x1C] = 700
        registers[0x30] = 0b01
        registers[0x31] = 9958
        registers[0x35] = 2
        registers[0x41], registers[0x42] = 0x0001, 0x86A0
        registers[0xF6] = 0x0AAB + 250
        line = self.lines_by_register(registers)
        assert "pack voltage" in line["0x006"] and "53.28 V" in line["0x006"]
        assert "-5.00 A" in line["0x008"]
        assert "cell 1 voltage" in line["0x00C"] and "3301 mV" in line["0x00C"]
        assert "cell temperature sensor 1" in line["0x01C"] and "20.0 °C" in line["0x01C"]
        assert "charge on, discharge off" in line["0x030"]
        assert "99.58 %" in line["0x031"]
        assert "discharging" in line["0x035"]
        assert "10000.0 Ah" in line["0x041"] and "(raw 0x000186A0)" in line["0x041"]
        assert "25.0 °C" in line["0x0F6"]
        assert "0x042" not in line  # consumed as the low word of the 32-bit field at 0x41

    def test_raw_hex_accompanies_every_decoded_value(self):
        registers = [0] * WINDOW_REGISTER_COUNT
        registers[0x06] = 5328
        assert "(raw 0x14D0)" in self.lines_by_register(registers)["0x006"]

    def test_unknown_registers_render_as_hex_rows(self):
        registers = [0] * WINDOW_REGISTER_COUNT
        registers[0x00] = 0xBEEF
        first_line = describe_window(registers).splitlines()[0]
        # The first unknown run spans registers 0x00-0x05, ended by the known field at 0x06.
        assert first_line == "0x000: BEEF 0000 0000 0000 0000 0000"

    def test_partial_window_truncating_a_32_bit_field_falls_back_to_hex(self):
        registers = [0] * 0x42  # ends between the two halves of "total charged" at 0x41
        text = describe_window(registers)
        assert "total charged" not in text
        assert text.splitlines()[-1] == "0x040: 0000 0000"
