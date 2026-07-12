from pathlib import Path

import pytest

from battery_bank.config import load_config
from battery_bank.core.bank import (
    BankInputs,
    ControlState,
    EventSeverity,
    SocSource,
    step_bank,
)
from battery_bank.core.charge_stage import ChargeStage
from battery_bank.core.values import AlarmSeverity, ShuntSnapshot
from tests.factories import make_alarms, make_snapshot

CONFIG = load_config(Path(__file__).parent.parent / "config.example.ini")

MAX_VOLTAGE = CONFIG.cells_per_pack * CONFIG.cell_voltage.max_volts


def make_packs(taken_at=1000.0, **overrides):
    return tuple(make_snapshot(unique_id=f"pack-{address}", address=address, taken_at_monotonic=taken_at, **overrides) for address in (1, 2, 3))


def make_shunt(taken_at=1000.0, current_amps=-5.0, soc_percent=82.5, consumed_ah=-17.5, aux_voltage_volts=0.777):
    return ShuntSnapshot(
        taken_at_monotonic=taken_at,
        current_amps=current_amps,
        soc_percent=soc_percent,
        consumed_ah=consumed_ah,
        aux_voltage_volts=aux_voltage_volts,
    )


def healthy_inputs():
    # The shunt aux voltage deviates only a few percent from the example PTC table's
    # expectation at the default pack temperature, well within max_deviation_percent.
    return BankInputs(packs=make_packs(), shunt=make_shunt())


def ready_state():
    """A ControlState past startup, so staleness responses apply instead of the startup grace."""
    return ControlState(startup_complete=True, first_step_at=0.0)


class TestHealthyBank:
    def step(self):
        return step_bank(CONFIG, ControlState(), healthy_inputs(), now_monotonic=1001.0)

    def test_controls_with_full_limits(self):
        _, decision, _ = self.step()
        assert decision.cvl_volts == pytest.approx(MAX_VOLTAGE)
        assert decision.ccl_amps == pytest.approx(10.0 * 3)
        assert decision.dcl_amps == pytest.approx(250.0 * 3)
        assert decision.allow_to_charge is True
        assert decision.allow_to_discharge is True
        assert decision.charge_stage is ChargeStage.BULK

    def test_shunt_is_the_authoritative_measurement_source(self):
        _, decision, _ = self.step()
        assert decision.soc_source is SocSource.SHUNT
        assert decision.soc_percent == pytest.approx(82.5)
        assert decision.current_amps == pytest.approx(-5.0)
        assert decision.consumed_ah == pytest.approx(-17.5)
        assert decision.voltage_volts == pytest.approx(53.0)
        assert decision.power_watts == pytest.approx(53.0 * -5.0)

    def test_no_alarms_and_no_error_events(self):
        _, decision, events = self.step()
        assert decision.cable_alarm is AlarmSeverity.OK
        assert all(event.severity is EventSeverity.INFO for event in events)


class TestStartupGrace:
    def test_incomplete_picture_during_grace_is_quiet_and_not_ready(self):
        inputs = BankInputs(packs=make_packs()[:2], shunt=None)
        _, decision, events = step_bank(CONFIG, ControlState(), inputs, now_monotonic=1001.0)
        assert decision.ready is False
        assert decision.cable_alarm is AlarmSeverity.OK
        assert events == ()

    def test_grace_expiry_without_complete_picture_fails_loud(self):
        inputs = BankInputs(packs=make_packs()[:2], shunt=None)
        state, _, _ = step_bank(CONFIG, ControlState(), inputs, now_monotonic=1001.0)
        stale_later = BankInputs(packs=make_packs(taken_at=1100.0)[:2], shunt=None)
        _, decision, events = step_bank(CONFIG, state, stale_later, now_monotonic=1101.0)
        assert decision.ready is False
        assert decision.cable_alarm is AlarmSeverity.ALARM
        assert any(event.severity is EventSeverity.ERROR for event in events)

    def test_completion_during_grace_activates_control(self):
        incomplete = BankInputs(packs=make_packs()[:2], shunt=None)
        state, _, _ = step_bank(CONFIG, ControlState(), incomplete, now_monotonic=1001.0)
        complete = BankInputs(packs=make_packs(taken_at=1002.0), shunt=make_shunt(taken_at=1002.0))
        _, decision, events = step_bank(CONFIG, state, complete, now_monotonic=1003.0)
        assert decision.ready is True
        assert decision.ccl_amps == pytest.approx(30.0)
        assert any("bank control active" in event.message for event in events)

    def test_readiness_persists_through_later_staleness(self):
        state, _, _ = step_bank(CONFIG, ready_state(), healthy_inputs(), now_monotonic=1001.0)
        _, decision, _ = step_bank(CONFIG, state, BankInputs(packs=make_packs()[:2], shunt=make_shunt(taken_at=1001.0)), now_monotonic=1002.0)
        assert decision.ready is True
        assert decision.ccl_amps == 0.0


class TestPackStaleness:
    def test_stale_pack_zeroes_limits_and_raises_cable_alarm(self):
        packs = make_packs()[:2] + (make_snapshot(unique_id="pack-3", address=3, taken_at_monotonic=900.0),)
        state, decision, events = step_bank(CONFIG, ready_state(), BankInputs(packs=packs, shunt=make_shunt()), now_monotonic=1001.0)
        assert decision.ccl_amps == 0.0
        assert decision.dcl_amps == 0.0
        assert decision.allow_to_charge is False
        assert decision.cable_alarm is AlarmSeverity.ALARM
        assert decision.all_packs_fresh is False
        assert any(event.severity is EventSeverity.ERROR and "2 of 3 packs" in event.message for event in events)

    def test_missing_pack_counts_as_stale(self):
        _, decision, _ = step_bank(CONFIG, ready_state(), BankInputs(packs=make_packs()[:2], shunt=make_shunt()), now_monotonic=1001.0)
        assert decision.ccl_amps == 0.0
        assert decision.cable_alarm is AlarmSeverity.ALARM

    def test_charge_stage_freezes_while_stale(self):
        state, _, _ = step_bank(CONFIG, ready_state(), healthy_inputs(), now_monotonic=1001.0)
        cvl_before = state.charge_stage.cvl_volts
        state, decision, _ = step_bank(CONFIG, state, BankInputs(packs=make_packs()[:2], shunt=make_shunt()), now_monotonic=1002.0)
        assert state.charge_stage.cvl_volts == cvl_before
        assert decision.cvl_volts == cvl_before

    def test_staleness_events_fire_only_on_edges(self):
        stale_inputs = BankInputs(packs=make_packs()[:2], shunt=make_shunt())
        state, _, first_events = step_bank(CONFIG, ready_state(), stale_inputs, now_monotonic=1001.0)
        stale_inputs_again = BankInputs(packs=make_packs()[:2], shunt=make_shunt(taken_at=1001.0))
        _, _, second_events = step_bank(CONFIG, state, stale_inputs_again, now_monotonic=1002.0)
        assert any("2 of 3" in event.message for event in first_events)
        assert not any("2 of 3" in event.message for event in second_events)

    def test_recovery_restores_limits_and_reports_it(self):
        state, _, _ = step_bank(CONFIG, ready_state(), BankInputs(packs=make_packs()[:2], shunt=make_shunt()), now_monotonic=1001.0)
        recovered = BankInputs(packs=make_packs(taken_at=1002.0), shunt=make_shunt(taken_at=1002.0))
        _, decision, events = step_bank(CONFIG, state, recovered, now_monotonic=1003.0)
        assert decision.ccl_amps == pytest.approx(30.0)
        assert any("reporting fresh data again" in event.message for event in events)


class TestShuntStaleness:
    def test_stale_shunt_zeroes_limits_and_falls_back_to_bms_soc(self):
        inputs = BankInputs(packs=make_packs(), shunt=make_shunt(taken_at=900.0))
        _, decision, events = step_bank(CONFIG, ready_state(), inputs, now_monotonic=1001.0)
        assert decision.ccl_amps == 0.0
        assert decision.soc_source is SocSource.BMS
        assert decision.soc_percent == pytest.approx(80.0)
        assert decision.cable_alarm is AlarmSeverity.WARNING
        assert any(event.severity is EventSeverity.ERROR and "Shunt data stale" in event.message for event in events)

    def test_missing_shunt_when_configured_zeroes_limits(self):
        _, decision, _ = step_bank(CONFIG, ready_state(), BankInputs(packs=make_packs(), shunt=None), now_monotonic=1001.0)
        assert decision.ccl_amps == 0.0
        assert decision.soc_source is SocSource.BMS

    def test_bms_fallback_uses_capacity_weighted_soc_and_summed_current(self):
        packs = (
            make_snapshot(unique_id="pack-1", address=1, soc_percent=100.0, full_capacity_ah=100.0, current_amps=-2.0),
            make_snapshot(unique_id="pack-2", address=2, soc_percent=50.0, full_capacity_ah=300.0, current_amps=-3.0),
            make_snapshot(unique_id="pack-3", address=3, soc_percent=50.0, full_capacity_ah=100.0, current_amps=-1.0),
        )
        _, decision, _ = step_bank(CONFIG, ControlState(), BankInputs(packs=packs, shunt=None), now_monotonic=1001.0)
        assert decision.soc_percent == pytest.approx((100 * 100 + 50 * 300 + 50 * 100) / 500)
        assert decision.current_amps == pytest.approx(-6.0)


class TestProtectionsIntegration:
    def test_temperature_spread_trip_zeroes_limits_with_error_event(self):
        packs = make_packs()[:2] + (
            make_snapshot(unique_id="pack-3", address=3, cell_temperatures_celsius=(35.0,) * 4),
        )
        state, decision, events = step_bank(CONFIG, ControlState(), BankInputs(packs=packs, shunt=make_shunt()), now_monotonic=1001.0)
        assert decision.ccl_amps == 0.0
        assert any(event.severity is EventSeverity.ERROR and "temperature spread" in event.message for event in events)
        # The trip latches: healthy inputs afterwards keep the limits at zero.
        _, decision, events = step_bank(CONFIG, state, healthy_inputs(), now_monotonic=1002.0)
        assert decision.ccl_amps == 0.0
        assert events == ()


class TestSocReset:
    def test_float_transition_requests_soc_reset_for_every_pack(self):
        full_packs_kwargs = dict(cell_voltages_volts=(3.61,) * 16, soc_percent=97.0)
        state = ControlState()
        now = 1000.0
        for advance in (1.0, 121.0):
            now += advance
            inputs = BankInputs(packs=make_packs(taken_at=now - 0.5, **full_packs_kwargs), shunt=make_shunt(taken_at=now - 0.5))
            state, decision, _ = step_bank(CONFIG, state, inputs, now_monotonic=now)
        assert decision.charge_stage is ChargeStage.FLOAT_TRANSITION
        assert decision.request_soc_reset_pack_ids == ("pack-1", "pack-2", "pack-3")


class TestAlarmAggregation:
    def test_worst_severity_per_category_wins(self):
        packs = make_packs()[:2] + (
            make_snapshot(unique_id="pack-3", address=3, alarms=make_alarms(high_temperature=AlarmSeverity.ALARM)),
        )
        _, decision, _ = step_bank(CONFIG, ControlState(), BankInputs(packs=packs, shunt=make_shunt()), now_monotonic=1001.0)
        assert decision.alarms.high_temperature is AlarmSeverity.ALARM
        assert decision.alarms.low_voltage is AlarmSeverity.OK

    def test_stale_pack_alarms_are_not_cleared(self):
        packs = make_packs()[:2] + (
            make_snapshot(unique_id="pack-3", address=3, taken_at_monotonic=900.0, alarms=make_alarms(internal_failure=AlarmSeverity.ALARM)),
        )
        _, decision, _ = step_bank(CONFIG, ControlState(), BankInputs(packs=packs, shunt=make_shunt()), now_monotonic=1001.0)
        assert decision.alarms.internal_failure is AlarmSeverity.ALARM
