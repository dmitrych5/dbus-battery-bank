import pytest

from battery_bank.transport.vedirect import VeDirectParser, parse_history_totals, parse_shunt_reading

SHUNT_FIELDS = {"V": "53000", "I": "-5000", "SOC": "825", "CE": "-17500", "VS": "777"}
HISTORY_FIELDS = {
    "H1": "-120000",
    "H2": "-80000",
    "H3": "-70000",
    "H4": "45",
    "H5": "1",
    "H6": "-52000000",
    "H7": "47500",
    "H8": "56100",
    "H10": "250",
    "H17": "140000",
    "H18": "150000",
    "PID": "0xA389",
}
"""The device alternates between the measurement frame and this history frame."""


def frame_bytes(fields: dict[str, str]) -> bytes:
    """A spec-compliant VE.Direct frame: CR LF before every field, and a checksum byte making
    the sum of all frame bytes zero modulo 256."""
    body = b"".join(b"\r\n" + key.encode() + b"\t" + value.encode() for key, value in fields.items()) + b"\r\nChecksum\t"
    return body + bytes([-sum(body) % 256])


def parse_stream(parser: VeDirectParser, frame_count=3, fields=SHUNT_FIELDS, corrupt_frame=None):
    """Feeds consecutive device frames; a frame's checksum line only completes when the next
    frame's CR LF arrives, so the last frame stays pending."""
    stream = b""
    for index in range(frame_count):
        frame = bytearray(frame_bytes(fields))
        if index == corrupt_frame:
            frame[5] ^= 0xFF
        stream += bytes(frame)
    return parser.feed(stream)


class TestVeDirectParser:
    def test_steady_state_frames_validate_and_parse(self):
        frames = parse_stream(VeDirectParser())
        assert len(frames) == 2
        assert frames[1].checksum_valid is True
        assert frames[1].fields["I"] == "-5000"

    def test_first_frame_after_connect_fails_its_checksum(self):
        frames = parse_stream(VeDirectParser())
        assert frames[0].checksum_valid is False

    def test_corrupted_byte_invalidates_the_frame(self):
        frames = parse_stream(VeDirectParser(), frame_count=4, corrupt_frame=2)
        assert [frame.checksum_valid for frame in frames] == [False, True, False]

    def test_byte_by_byte_feeding_matches_bulk_feeding(self):
        stream = frame_bytes(SHUNT_FIELDS) * 3
        parser = VeDirectParser()
        frames = [frame for byte in stream for frame in parser.feed(bytes([byte]))]
        assert [frame.checksum_valid for frame in frames] == [False, True]

    def test_oversized_garbage_is_discarded_without_breaking_later_frames(self):
        parser = VeDirectParser()
        assert parser.feed(b"\xff" * 100_000) == []
        frames = parse_stream(parser)
        assert frames[-1].checksum_valid is True


class TestParseShuntReading:
    def valid_frame(self, fields=SHUNT_FIELDS):
        return parse_stream(VeDirectParser(), fields=fields)[1]

    def test_extracts_scaled_values(self):
        reading = parse_shunt_reading(self.valid_frame())
        assert reading.current_amps == pytest.approx(-5.0)
        assert reading.soc_percent == pytest.approx(82.5)
        assert reading.consumed_ah == pytest.approx(-17.5)
        assert reading.aux_voltage_volts == pytest.approx(0.777)

    def test_missing_aux_voltage_is_none(self):
        fields = {key: value for key, value in SHUNT_FIELDS.items() if key != "VS"}
        reading = parse_shunt_reading(self.valid_frame(fields))
        assert reading is not None
        assert reading.aux_voltage_volts is None

    def test_missing_required_field_yields_no_reading(self):
        fields = {key: value for key, value in SHUNT_FIELDS.items() if key != "I"}
        assert parse_shunt_reading(self.valid_frame(fields)) is None

    def test_invalid_checksum_yields_no_reading(self):
        first_frame = parse_stream(VeDirectParser())[0]
        assert first_frame.checksum_valid is False
        assert parse_shunt_reading(first_frame) is None

    def test_non_numeric_value_yields_no_reading(self):
        assert parse_shunt_reading(self.valid_frame({**SHUNT_FIELDS, "I": "garbage"})) is None


class TestParseHistoryTotals:
    def valid_frame(self, fields):
        return parse_stream(VeDirectParser(), fields=fields)[1]

    def test_extracts_lifetime_totals_from_the_history_frame(self):
        totals = parse_history_totals(self.valid_frame(HISTORY_FIELDS))
        assert totals.deepest_discharge_ah == pytest.approx(-120.0)
        assert totals.last_discharge_ah == pytest.approx(-80.0)
        assert totals.average_discharge_ah == pytest.approx(-70.0)
        assert totals.charge_cycles == 45
        assert totals.full_discharge_count == 1
        assert totals.total_ah_drawn_ah == pytest.approx(-52_000.0)
        assert totals.minimum_voltage_volts == pytest.approx(47.5)
        assert totals.maximum_voltage_volts == pytest.approx(56.1)
        assert totals.automatic_sync_count == 250
        assert totals.discharged_energy_kwh == pytest.approx(1400.0)
        assert totals.charged_energy_kwh == pytest.approx(1500.0)

    def test_partial_history_frame_yields_no_totals(self):
        fields = {key: value for key, value in HISTORY_FIELDS.items() if key != "H4"}
        assert parse_history_totals(self.valid_frame(fields)) is None

    def test_measurement_frame_yields_no_totals(self):
        assert parse_history_totals(self.valid_frame(SHUNT_FIELDS)) is None

    def test_history_frame_yields_no_reading(self):
        assert parse_shunt_reading(self.valid_frame(HISTORY_FIELDS)) is None
