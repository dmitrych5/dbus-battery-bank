import pytest

from battery_bank.config import CurrentLimitConfig, LimitUpdatePolicyConfig
from battery_bank.core.current_limits import (
    BankCurrentLimit,
    CurrentDirection,
    CurrentLimitState,
    LimitSource,
    compute_bank_current_limit,
    compute_pack_current_limit,
)
from battery_bank.core.interpolation import LimitCurve
from battery_bank.core.values import BmsLimits, ChainAggregatedLimits
from tests.factories import make_snapshot

WIDE_OPEN_CURVE = LimitCurve(inputs=(-100.0, 100.0), outputs=(1.0, 1.0))

LIMIT_CONFIG = CurrentLimitConfig(
    max_amps=10.0,
    cell_voltage_curve=LimitCurve(inputs=(3.375, 3.450, 3.600, 3.630), outputs=(1.0, 0.25, 0.02, 0.0)),
    cell_temperature_curve=LimitCurve(inputs=(2.0, 3.0, 5.0, 10.0, 42.0, 48.0), outputs=(0.0, 0.2, 0.4, 1.0, 1.0, 0.0)),
    ambient_temperature_curve=LimitCurve(inputs=(2.0, 10.0, 42.0, 48.0), outputs=(0.0, 1.0, 1.0, 0.0)),
    mosfet_temperature_curve=LimitCurve(inputs=(70.0, 80.0, 90.0), outputs=(1.0, 0.25, 0.0)),
    zero_recovery_min_fraction=0.015,
)

UPDATE_POLICY = LimitUpdatePolicyConfig(min_update_interval_seconds=60.0, immediate_update_change_fraction=0.33)


def pack_limit(pack, direction=CurrentDirection.CHARGE, chain_limit_amps=None):
    return compute_pack_current_limit(LIMIT_CONFIG, direction, pack, chain_limit_amps)


class TestPackLimit:
    def test_healthy_pack_is_limited_by_configured_maximum(self):
        result = pack_limit(make_snapshot())
        assert result.amps == pytest.approx(10.0)
        assert result.active_sources == (LimitSource.MAX_CURRENT,)

    def test_bms_limit_below_maximum_wins(self):
        pack = make_snapshot(bms_limits=BmsLimits(5.0, 300.0, 55.2, 44.3))
        result = pack_limit(pack)
        assert result.amps == pytest.approx(5.0)
        assert result.active_sources == (LimitSource.BMS,)

    def test_charge_uses_highest_cell_voltage(self):
        pack = make_snapshot(cell_voltages_volts=(3.3,) * 15 + (3.45,))
        result = pack_limit(pack)
        assert result.amps == pytest.approx(2.5)
        assert result.active_sources == (LimitSource.CELL_VOLTAGE,)

    def test_discharge_uses_lowest_cell_voltage(self):
        discharge_config = CurrentLimitConfig(
            max_amps=250.0,
            cell_voltage_curve=LimitCurve(inputs=(2.709, 2.710), outputs=(0.0, 1.0)),
            cell_temperature_curve=WIDE_OPEN_CURVE,
            ambient_temperature_curve=WIDE_OPEN_CURVE,
            mosfet_temperature_curve=WIDE_OPEN_CURVE,
            zero_recovery_min_fraction=0.015,
        )
        pack = make_snapshot(cell_voltages_volts=(3.3,) * 15 + (2.705,))
        result = compute_pack_current_limit(discharge_config, CurrentDirection.DISCHARGE, pack, None)
        assert result.amps == pytest.approx(0.0)
        assert result.active_sources == (LimitSource.CELL_VOLTAGE,)

    def test_cell_temperature_worst_of_coldest_and_hottest_sensor(self):
        cold_limited = pack_limit(make_snapshot(cell_temperatures_celsius=(4.0, 20.0, 20.0, 20.0)))
        assert cold_limited.amps == pytest.approx(10.0 * 0.3)
        hot_limited = pack_limit(make_snapshot(cell_temperatures_celsius=(20.0, 20.0, 20.0, 45.0)))
        assert hot_limited.amps == pytest.approx(10.0 * 0.5)
        assert hot_limited.active_sources == (LimitSource.CELL_TEMPERATURE,)

    def test_ambient_temperature_limits(self):
        result = pack_limit(make_snapshot(ambient_temperature_celsius=45.0))
        assert result.amps == pytest.approx(5.0)
        assert result.active_sources == (LimitSource.AMBIENT_TEMPERATURE,)

    def test_mosfet_temperature_limits(self):
        result = pack_limit(make_snapshot(mosfet_temperature_celsius=85.0))
        assert result.amps == pytest.approx(1.25)
        assert result.active_sources == (LimitSource.MOSFET_TEMPERATURE,)

    def test_disabled_fet_forces_zero(self):
        result = pack_limit(make_snapshot(charge_fet_enabled=False))
        assert result.amps == 0.0
        assert result.active_sources == (LimitSource.FET_OFF,)

    def test_discharge_fet_does_not_affect_charge(self):
        result = pack_limit(make_snapshot(discharge_fet_enabled=False))
        assert result.amps == pytest.approx(10.0)

    def test_chain_limit_applies(self):
        result = pack_limit(make_snapshot(), chain_limit_amps=3.0)
        assert result.amps == pytest.approx(3.0)
        assert result.active_sources == (LimitSource.CHAIN_MASTER,)

    def test_limit_equal_to_maximum_is_not_reported_as_a_restriction(self):
        pack = make_snapshot(bms_limits=BmsLimits(10.0, 300.0, 55.2, 44.3))
        result = pack_limit(pack)
        assert result.active_sources == (LimitSource.MAX_CURRENT,)

    def test_ties_between_genuine_restrictions_report_all_sources(self):
        pack = make_snapshot(bms_limits=BmsLimits(5.0, 300.0, 55.2, 44.3))
        result = pack_limit(pack, chain_limit_amps=5.0)
        assert set(result.active_sources) == {LimitSource.BMS, LimitSource.CHAIN_MASTER}


class TestBankLimit:
    def bank(self, packs, state=CurrentLimitState(), now=0.0) -> BankCurrentLimit:
        return compute_bank_current_limit(LIMIT_CONFIG, UPDATE_POLICY, CurrentDirection.CHARGE, packs, state, now)

    def test_lowest_pack_limit_times_pack_count(self):
        packs = [
            make_snapshot(unique_id="pack-1", address=1),
            make_snapshot(unique_id="pack-2", address=2, ambient_temperature_celsius=45.0),
            make_snapshot(unique_id="pack-3", address=3),
        ]
        result = self.bank(packs)
        assert result.candidate_amps == pytest.approx(5.0 * 3)
        assert result.limiting_pack_unique_id == "pack-2"
        assert result.active_sources == (LimitSource.AMBIENT_TEMPERATURE,)

    def test_chain_master_limit_reaches_all_packs_on_its_port(self):
        packs = [
            make_snapshot(unique_id="pack-1", address=1, chain_aggregated_limits=ChainAggregatedLimits(4.0, 100.0)),
            make_snapshot(unique_id="pack-2", address=2),
        ]
        result = self.bank(packs)
        assert result.candidate_amps == pytest.approx(4.0 * 2)
        assert all(pack.amps == pytest.approx(4.0) for pack in result.per_pack)

    def test_chain_master_limit_does_not_cross_ports(self):
        packs = [
            make_snapshot(unique_id="pack-1", port="/dev/ttyUSB0", chain_aggregated_limits=ChainAggregatedLimits(4.0, 100.0)),
            make_snapshot(unique_id="pack-2", port="/dev/ttyUSB1", address=1),
        ]
        result = self.bank(packs)
        per_pack_amps = {pack.pack_unique_id: pack.amps for pack in result.per_pack}
        assert per_pack_amps["pack-1"] == pytest.approx(4.0)
        assert per_pack_amps["pack-2"] == pytest.approx(10.0)


class TestUpdatePolicy:
    def bank(self, ambient, state, now) -> BankCurrentLimit:
        packs = [make_snapshot(ambient_temperature_celsius=ambient)]
        return compute_bank_current_limit(LIMIT_CONFIG, UPDATE_POLICY, CurrentDirection.CHARGE, packs, state, now)

    def test_first_value_publishes_immediately(self):
        result = self.bank(ambient=25.0, state=CurrentLimitState(), now=0.0)
        assert result.published_amps == pytest.approx(10.0)

    def test_small_change_within_interval_keeps_published_value(self):
        state = CurrentLimitState(published_amps=10.0, published_at_monotonic=0.0)
        result = self.bank(ambient=43.0, state=state, now=30.0)
        assert result.candidate_amps == pytest.approx(10.0 * 5 / 6)
        assert result.published_amps == pytest.approx(10.0)

    def test_small_change_publishes_after_interval(self):
        state = CurrentLimitState(published_amps=10.0, published_at_monotonic=0.0)
        result = self.bank(ambient=43.0, state=state, now=61.0)
        assert result.published_amps == pytest.approx(10.0 * 5 / 6)

    def test_large_change_publishes_immediately(self):
        state = CurrentLimitState(published_amps=10.0, published_at_monotonic=0.0)
        result = self.bank(ambient=46.0, state=state, now=1.0)
        assert result.published_amps == pytest.approx(10.0 / 3)

    def test_drop_to_zero_publishes_immediately(self):
        state = CurrentLimitState(published_amps=10.0, published_at_monotonic=0.0)
        result = self.bank(ambient=50.0, state=state, now=1.0)
        assert result.published_amps == 0.0

    def test_recovery_below_threshold_is_held_at_zero(self):
        state = CurrentLimitState(published_amps=0.0, published_at_monotonic=0.0)
        # Fraction recovers to 0.01, below zero_recovery_min_fraction of 0.015.
        result = self.bank(ambient=47.94, state=state, now=1.0)
        assert result.candidate_amps == pytest.approx(0.1, abs=0.01)
        assert result.published_amps == 0.0
        assert result.held_at_zero is True

    def test_recovery_above_threshold_publishes(self):
        state = CurrentLimitState(published_amps=0.0, published_at_monotonic=0.0)
        result = self.bank(ambient=42.0, state=state, now=1.0)
        assert result.published_amps == pytest.approx(10.0)
        assert result.held_at_zero is False
