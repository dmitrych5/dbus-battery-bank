from dataclasses import replace

import pytest

from battery_bank.acquisition.availability import (
    MAX_AVAILABILITY_RETRIES,
    AvailabilityStatus,
    CommandAvailabilityTracker,
)
from battery_bank.acquisition.snapshots import assemble_snapshot, build_unique_id, select_soc_percent
from battery_bank.core.values import AlarmSeverity, PackIdentity
from battery_bank.transport.up16s import IndividualPackStatus, PackParams2
from tests.factories import make_pack_status, make_params2, make_raw_status


def identity(address=2):
    return PackIdentity(unique_id=f"pack-{address}", port="/dev/ttyUSB0", address=address)


def snapshot(
    address=2, pack_status=None, raw_status=None, params2=None, individual_status=None, high_res_soc_known_available=False, previous_soc=None
):
    return assemble_snapshot(
        identity(address),
        pack_status if pack_status is not None else make_pack_status(),
        raw_status,
        params2,
        individual_status,
        high_res_soc_known_available,
        previous_soc,
        now_monotonic=1000.0,
    )


class TestAssembleSnapshot:
    def test_converts_measurements_to_engineering_units(self):
        result = snapshot(params2=make_params2())
        assert result.voltage_volts == pytest.approx(13.21)
        assert result.current_amps == pytest.approx(-5.0)
        assert result.full_capacity_ah == pytest.approx(100.0)
        assert result.cell_voltages_volts == (3.301, 3.302, 3.303, 3.304)
        assert result.cells_balancing == (True, True, False, False)
        assert result.cell_temperatures_celsius == (20.0, 19.5)
        assert result.mosfet_temperature_celsius == pytest.approx(25.0)
        assert result.ambient_temperature_celsius == pytest.approx(30.0)
        assert result.total_discharge_ah == pytest.approx(1234.5)
        assert result.charge_fet_enabled is True
        assert result.discharge_fet_enabled is True

    def test_decodes_fet_flags_and_alarms(self):
        result = snapshot(pack_status=make_pack_status(mosfet_flags=0b10, fault_flags=1 << 20))
        assert result.charge_fet_enabled is True
        assert result.discharge_fet_enabled is False
        assert result.alarms.internal_failure is AlarmSeverity.ALARM

    def test_slave_carries_no_chain_limits(self):
        result = snapshot(address=2)
        assert result.chain_aggregated_limits is None
        assert result.bms_limits.charge_current_amps == pytest.approx(10.0)

    def test_master_splits_chain_and_own_limits(self):
        individual = IndividualPackStatus(unused=b"\x00" * 96, charge_current_limit=120, discharge_current_limit=2000)
        result = snapshot(address=1, individual_status=individual)
        assert result.chain_aggregated_limits.charge_current_amps == pytest.approx(10.0)
        assert result.chain_aggregated_limits.discharge_current_amps == pytest.approx(250.0)
        assert result.bms_limits.charge_current_amps == pytest.approx(12.0)
        assert result.bms_limits.discharge_current_amps == pytest.approx(200.0)

    def test_master_without_individual_status_uses_aggregated_values_as_own(self):
        result = snapshot(address=1)
        assert result.chain_aggregated_limits is not None
        assert result.bms_limits.charge_current_amps == pytest.approx(10.0)

    def test_raw_status_supplies_the_live_readings(self):
        raw = replace(
            make_raw_status(),
            raw_current=29450,
            pack_voltage=1350,
            soc=8123,
            residual_capacity=8100,
            mosfet_temp=760,
            ambient_temp=810,
            cell_balancing_flags=0b0100,
            mosfet_state=0b01,
        )
        result = snapshot(raw_status=raw)
        assert result.current_amps == pytest.approx(-5.5)  # the pre-deadband register, not PackStatus's -5.0
        assert result.voltage_volts == pytest.approx(13.50)
        assert result.soc_percent == pytest.approx(81.23)
        assert result.remaining_capacity_ah == pytest.approx(81.0)
        assert result.mosfet_temperature_celsius == pytest.approx(26.0)
        assert result.ambient_temperature_celsius == pytest.approx(31.0)
        assert result.cell_voltages_volts == (3.301, 3.302, 3.303, 3.304)
        assert result.cell_temperatures_celsius == (20.0, 19.5)
        assert result.cells_balancing == (False, False, True, False)
        # The window's bit order is the opposite of PackStatus's: bit 0 is the charge MOS.
        assert result.charge_fet_enabled is True
        assert result.discharge_fet_enabled is False
        assert result.total_discharge_ah is None

    def test_raw_status_limits_are_the_packs_own_even_on_the_master(self):
        raw = replace(make_raw_status(), charge_current_limit=120, discharge_current_limit=2000)
        result = snapshot(address=1, raw_status=raw)
        assert result.bms_limits.charge_current_amps == pytest.approx(12.0)
        assert result.bms_limits.discharge_current_amps == pytest.approx(200.0)
        # PackStatus stays the source of the chain-aggregated limits.
        assert result.chain_aggregated_limits.charge_current_amps == pytest.approx(10.0)

    def test_raw_status_slices_the_fixed_slots_to_the_pack_counts(self):
        result = snapshot(raw_status=make_raw_status())
        assert len(result.cell_voltages_volts) == 4
        assert len(result.cells_balancing) == 4
        assert len(result.cell_temperatures_celsius) == 2


class TestSocSelection:
    def test_high_res_soc_wins_when_available(self):
        assert select_soc_percent(make_pack_status(), 8123, True, previous_soc_percent=75.0) == pytest.approx(81.23)

    def test_transient_high_res_loss_keeps_previous_soc(self):
        soc = select_soc_percent(make_pack_status(soc=8000), None, high_res_soc_known_available=True, previous_soc_percent=80.5)
        assert soc == pytest.approx(80.5)

    def test_large_divergence_falls_back_to_pack_status_soc(self):
        soc = select_soc_percent(make_pack_status(soc=8000), None, high_res_soc_known_available=True, previous_soc_percent=85.0)
        assert soc == pytest.approx(80.0)

    def test_unavailable_high_res_source_uses_pack_status_soc(self):
        soc = select_soc_percent(make_pack_status(soc=8000), None, high_res_soc_known_available=False, previous_soc_percent=80.5)
        assert soc == pytest.approx(80.0)


class TestUniqueId:
    def test_prefers_bms_model_and_serial(self):
        assert build_unique_id("UP16S015-SN123", "PACK-9", 105.0) == "UP16S015-SN123"

    def test_falls_back_to_pack_serial_with_rated_capacity(self):
        assert build_unique_id(None, "PACK-9", 105.0) == "PACK-9_105.0"


class TestAvailabilityTracker:
    def test_unknown_commands_are_sent(self):
        tracker = CommandAvailabilityTracker()
        assert tracker.should_send(1, PackParams2) is True

    def test_success_makes_available_and_reports_the_transition_once(self):
        tracker = CommandAvailabilityTracker()
        assert tracker.record_success(1, PackParams2) is True
        assert tracker.record_success(1, PackParams2) is False
        assert tracker.status(1, PackParams2) is AvailabilityStatus.AVAILABLE

    def test_repeated_failures_make_unavailable_and_stop_sending(self):
        tracker = CommandAvailabilityTracker()
        transitions = [tracker.record_failure(1, PackParams2) for _ in range(MAX_AVAILABILITY_RETRIES)]
        assert transitions == [False] * (MAX_AVAILABILITY_RETRIES - 1) + [True]
        assert tracker.should_send(1, PackParams2) is False

    def test_failures_after_success_do_not_revoke_availability(self):
        tracker = CommandAvailabilityTracker()
        tracker.record_success(1, PackParams2)
        for _ in range(MAX_AVAILABILITY_RETRIES + 1):
            tracker.record_failure(1, PackParams2)
        assert tracker.status(1, PackParams2) is AvailabilityStatus.AVAILABLE

    def test_mark_unavailable_revokes_availability(self):
        tracker = CommandAvailabilityTracker()
        tracker.record_success(1, PackParams2)
        tracker.mark_unavailable(1, PackParams2)
        assert tracker.should_send(1, PackParams2) is False

    def test_addresses_are_tracked_independently(self):
        tracker = CommandAvailabilityTracker()
        for _ in range(MAX_AVAILABILITY_RETRIES):
            tracker.record_failure(1, PackParams2)
        assert tracker.should_send(1, PackParams2) is False
        assert tracker.should_send(2, PackParams2) is True
