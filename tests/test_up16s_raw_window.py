from dataclasses import replace

import pytest

from battery_bank.transport.up16s import FrameError, build_request, parse_response
from battery_bank.transport.up16s_raw_window import RawStatus, from_raw_window_current_to_amps, validate
from tests.factories import make_pack_status, make_raw_status, make_raw_status_registers, raw_status_payload
from tests.test_up16s import response_frame


def failures_for(raw_status, pack_status=None, address=2):
    return validate(raw_status, pack_status if pack_status is not None else make_pack_status(), address)


class TestCodec:
    def test_request_covers_registers_0x02_through_0x52(self):
        request = build_request(2, RawStatus)
        assert request[:8] == bytes.fromhex("02 78 30 02 30 53 00 00".replace(" ", ""))

    def test_fields_parse_from_their_documented_registers(self):
        raw = make_raw_status(address=2)
        assert raw.raw_current == 29500
        assert raw.pack_voltage == 1321
        assert raw.current == 29500
        assert raw.cell_voltages == (3301, 3302, 3303, 3304) + (0,) * 12
        assert raw.temperatures == (700, 695, 0, 0)
        assert raw.max_v_cell_num == 4
        assert raw.max_cell_voltage == 3304
        assert raw.min_t_sensor_num == 2
        assert raw.mosfet_state == 0b11
        assert raw.soc == 8000
        assert raw.residual_capacity == 8000
        assert raw.operation_status == 2
        assert raw.avg_cell_voltage == 3302
        assert raw.charge_current_limit == 100
        assert raw.discharge_current_limit == 2500
        assert raw.maximum_charge_voltage == 552
        assert raw.minimum_discharge_voltage == 443
        assert raw.cell_balancing_flags == 0b0011
        assert raw.address == 2

    def test_truncated_payload_raises(self):
        with pytest.raises(FrameError, match="cannot unpack"):
            parse_response(2, RawStatus, response_frame(2, RawStatus, b"\x00" * 10))

    def test_window_current_conversion(self):
        assert from_raw_window_current_to_amps(29500) == pytest.approx(-5.0)


class TestValidate:
    def test_consistent_response_passes(self):
        assert failures_for(make_raw_status()) == []

    def test_realistic_drift_of_the_master_cache_passes(self):
        """A slave's PackStatus can be ~50 s stale; realistic drift between the two sources
        must never fail validation — a false positive disables the command until restart."""
        drifted = make_pack_status(
            pack_voltage=1521,  # +2 V after a load step
            soc=8300,
            cell_voltages=(3351, 3352, 3353, 3354),
        )
        drifted = replace(drifted, remaining_capacity=8500, mosfet_temp=850, ambient_temp=750, temperatures=(750, 745))
        assert failures_for(make_raw_status(), drifted) == []

    def test_wrong_battery_address_fails_with_both_values(self):
        failures = failures_for(make_raw_status(address=3), address=2)
        assert len(failures) == 1
        assert "register 0x52 says 3" in failures[0]
        assert "addressed to 2" in failures[0]

    def test_extreme_tracker_disagreement_fails_naming_both_values(self):
        raw = replace(make_raw_status(), max_cell_voltage=3400)
        failures = failures_for(raw)
        assert any("max cell voltage" in failure and "3400" in failure and "3304" in failure for failure in failures)

    def test_extreme_locator_out_of_range_fails(self):
        raw = replace(make_raw_status(), max_v_cell_num=7)
        assert any("#7" in failure and "1..4" in failure for failure in failures_for(raw))

    def test_cell_sum_disagreeing_with_pack_voltage_fails(self):
        raw = replace(make_raw_status(), pack_voltage=1621)  # 16.21 V vs the 13.21 V cell sum
        assert any("cell sum" in failure for failure in failures_for(raw))

    def test_pre_deadband_current_disagreeing_with_deadbanded_fails(self):
        raw = replace(make_raw_status(), raw_current=28500)  # -15 A vs -5 A deadbanded
        failures = failures_for(raw)
        assert any("pre-deadband" in failure and "-15" in failure and "-5" in failure for failure in failures)

    def test_operation_status_contradicting_the_current_sign_fails(self):
        raw = replace(make_raw_status(), raw_current=28500, current=28500, operation_status=1)
        assert any("charging" in failure and "-15" in failure for failure in failures_for(raw))

    def test_out_of_sane_range_cell_fails(self):
        raw = make_raw_status()
        raw = replace(raw, cell_voltages=(800,) + raw.cell_voltages[1:])
        assert any("cell 1 voltage" in failure and "sane range" in failure for failure in failures_for(raw))

    def test_limit_out_of_sane_range_fails(self):
        raw = replace(make_raw_status(), charge_current_limit=50000)  # 5000 A
        assert any("charge current limit" in failure and "sane range" in failure for failure in failures_for(raw))

    def test_soc_diverging_from_pack_status_fails_naming_the_source(self):
        failures = failures_for(make_raw_status(), make_pack_status(soc=9000))
        assert any("SoC" in failure and "80" in failure and "90" in failure and "PackStatus" in failure for failure in failures)

    def test_balancing_bits_beyond_the_cell_count_fail(self):
        raw = replace(make_raw_status(), cell_balancing_flags=0b10011)
        assert any("balancing" in failure for failure in failures_for(raw))

    def test_registers_beyond_the_cell_and_sensor_counts_are_ignored(self):
        """The window always carries 16 cell and 4 sensor slots; the unused ones read 0 and
        must not trip the sane-range checks on a smaller pack."""
        registers = make_raw_status_registers()
        assert registers[0x10] == 0 and registers[0x1E] == 0
        assert validate(RawStatus.from_payload(raw_status_payload(registers)), make_pack_status(), 2) == []
