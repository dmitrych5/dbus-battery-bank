import dataclasses
from pathlib import Path

import pytest

from battery_bank.config import load_config
from battery_bank.core.bank import BankInputs, ControlState, step_bank
from battery_bank.core.charge_stage import ChargeStage
from battery_bank.core.history import HistoryState, HistoryValues, clear_history, step_history
from battery_bank.core.values import AlarmSeverity
from battery_bank.persistence.state_file import StateFile, StateFileError, to_persisted
from battery_bank.publishing.service_values import history_service_values
from tests.factories import make_alarms, make_snapshot
from tests.test_bank import healthy_inputs, make_packs, make_shunt

CONFIG = load_config(Path(__file__).parent.parent / "config.example.ini")


def make_decision(**overrides):
    """A healthy bank decision with selected fields overridden, so each test states only the
    inputs the accumulator should react to."""
    _, decision, _ = step_bank(CONFIG, ControlState(), healthy_inputs(), now_monotonic=1001.0)
    return dataclasses.replace(decision, **overrides) if overrides else decision


def stepped(state=HistoryState(), now=1000.0, wall=5_000_000.0, packs=(), **decision_overrides):
    return step_history(state, make_decision(**decision_overrides), packs, now, wall)


class TestExtremes:
    def test_voltage_extremes_track_the_bank_voltage(self):
        state = stepped(voltage_volts=52.0)
        state = stepped(state, voltage_volts=54.5)
        state = stepped(state, voltage_volts=53.0)
        assert state.values.minimum_voltage_volts == pytest.approx(52.0)
        assert state.values.maximum_voltage_volts == pytest.approx(54.5)

    def test_no_voltage_leaves_extremes_untouched(self):
        state = stepped(voltage_volts=None)
        assert state.values.minimum_voltage_volts is None

    def test_cell_and_temperature_extremes_span_all_packs(self):
        packs = (
            make_snapshot(cell_voltages_volts=(3.1,) + (3.3,) * 15, cell_temperatures_celsius=(5.0, 20.0)),
            make_snapshot(unique_id="pack-2", cell_voltages_volts=(3.3,) * 15 + (3.45,), cell_temperatures_celsius=(21.0, 39.0)),
        )
        state = stepped(packs=packs)
        assert state.values.minimum_cell_voltage_volts == pytest.approx(3.1)
        assert state.values.maximum_cell_voltage_volts == pytest.approx(3.45)
        assert state.values.minimum_temperature_celsius == pytest.approx(5.0)
        assert state.values.maximum_temperature_celsius == pytest.approx(39.0)


class TestVoltageAlarmCounts:
    def test_rising_edge_counts_once_until_cleared(self):
        state = stepped()
        state = stepped(state, alarms=make_alarms(low_voltage=AlarmSeverity.ALARM))
        state = stepped(state, alarms=make_alarms(low_voltage=AlarmSeverity.ALARM))
        assert state.values.low_voltage_alarm_count == 1
        state = stepped(state)
        state = stepped(state, alarms=make_alarms(low_cell_voltage=AlarmSeverity.WARNING))
        assert state.values.low_voltage_alarm_count == 2

    def test_alarm_already_active_at_start_is_not_counted(self):
        """After a restart the pre-restart edge was already counted (or lost with its crash);
        adopting the active state without counting avoids double counting."""
        state = stepped(alarms=make_alarms(high_voltage=AlarmSeverity.ALARM))
        assert state.values.high_voltage_alarm_count == 0
        state = stepped(state, alarms=make_alarms(high_voltage=AlarmSeverity.ALARM))
        assert state.values.high_voltage_alarm_count == 0

    def test_cell_and_pack_flags_share_one_counter(self):
        state = stepped()
        state = stepped(state, alarms=make_alarms(high_voltage=AlarmSeverity.ALARM, high_cell_voltage=AlarmSeverity.ALARM))
        assert state.values.high_voltage_alarm_count == 1


class TestDischargeCycles:
    def test_running_cycle_depth_follows_the_deepest_consumed_ah(self):
        state = stepped(consumed_ah=-30.0)
        state = stepped(state, consumed_ah=-80.0)
        state = stepped(state, consumed_ah=-40.0)
        assert state.values.cycle_discharge_ah == pytest.approx(80.0)
        assert state.values.deepest_discharge_ah == pytest.approx(80.0)
        assert state.values.last_discharge_ah is None

    def test_full_charge_finalizes_the_cycle(self):
        state = stepped(consumed_ah=-80.0)
        state = stepped(state, consumed_ah=-1.0, entered_float_transition=True, wall=6_000_000.0)
        assert state.values.last_discharge_ah == pytest.approx(80.0)
        assert state.values.cycle_discharge_ah == 0.0
        assert state.values.discharge_cycle_count == 1
        assert state.values.last_full_charge_at_wall_seconds == pytest.approx(6_000_000.0)

    def test_average_discharge_over_completed_cycles(self):
        state = stepped(consumed_ah=-80.0)
        state = stepped(state, entered_float_transition=True)
        state = stepped(state, consumed_ah=-40.0)
        state = stepped(state, entered_float_transition=True)
        assert state.values.average_discharge_ah() == pytest.approx(60.0)
        assert state.values.last_discharge_ah == pytest.approx(40.0)

    def test_full_charge_without_discharge_stamps_but_adds_no_cycle(self):
        state = stepped(entered_float_transition=True, consumed_ah=0.0)
        assert state.values.discharge_cycle_count == 0
        assert state.values.last_full_charge_at_wall_seconds is not None

    def test_stale_shunt_does_not_fake_a_deeper_discharge(self):
        """The BMS fallback counts consumed Ah from a different zero; a momentary source
        switch must not corrupt the depth records."""
        state = stepped(consumed_ah=-30.0)
        state = stepped(state, consumed_ah=-120.0, shunt_fresh=False)
        assert state.values.deepest_discharge_ah == pytest.approx(30.0)

    def test_positive_consumed_ah_counts_as_zero_depth(self):
        state = stepped(consumed_ah=0.5)
        assert state.values.deepest_discharge_ah == pytest.approx(0.0)


class TestEnergyIntegration:
    def test_charge_and_discharge_split_by_power_sign(self):
        state = stepped(now=1000.0, power_watts=3600.0)
        state = stepped(state, now=1001.0, power_watts=3600.0)
        state = stepped(state, now=1002.0, power_watts=-7200.0)
        assert state.values.charged_energy_kwh == pytest.approx(0.001)
        assert state.values.discharged_energy_kwh == pytest.approx(0.002)

    def test_first_step_and_long_gaps_are_not_integrated(self):
        state = stepped(now=1000.0, power_watts=3_600_000.0)
        assert state.values.charged_energy_kwh == 0.0
        state = stepped(state, now=1031.0, power_watts=3_600_000.0)
        assert state.values.charged_energy_kwh == 0.0
        state = stepped(state, now=1032.0, power_watts=3_600_000.0)
        assert state.values.charged_energy_kwh == pytest.approx(1.0)

    def test_no_power_reading_is_skipped(self):
        state = stepped(now=1000.0)
        state = stepped(state, now=1001.0, power_watts=None)
        assert state.values.charged_energy_kwh == 0.0


def populated_values() -> HistoryValues:
    return HistoryValues(
        minimum_voltage_volts=48.0,
        maximum_voltage_volts=56.0,
        minimum_cell_voltage_volts=3.0,
        maximum_cell_voltage_volts=3.5,
        minimum_temperature_celsius=4.0,
        maximum_temperature_celsius=35.0,
        low_voltage_alarm_count=2,
        high_voltage_alarm_count=1,
        deepest_discharge_ah=120.0,
        last_discharge_ah=80.0,
        cycle_discharge_ah=15.0,
        discharge_cycle_count=10,
        discharge_cycle_ah_total=700.0,
        charged_energy_kwh=500.0,
        discharged_energy_kwh=450.0,
        last_full_charge_at_wall_seconds=1_000_000.0,
    )


class TestClear:
    def test_clear_everything(self):
        state = clear_history(HistoryState(values=populated_values()), 1)
        assert state.values == HistoryValues()

    def test_clear_one_category_leaves_the_others(self):
        state = clear_history(HistoryState(values=populated_values()), 6)
        assert state.values.minimum_temperature_celsius is None
        assert state.values.maximum_temperature_celsius is None
        assert state.values.minimum_voltage_volts == pytest.approx(48.0)
        assert state.values.discharge_cycle_count == 10

    def test_clear_capacity_resets_all_discharge_bookkeeping(self):
        state = clear_history(HistoryState(values=populated_values()), 2)
        assert state.values.deepest_discharge_ah is None
        assert state.values.last_discharge_ah is None
        assert state.values.cycle_discharge_ah == 0.0
        assert state.values.discharge_cycle_count == 0
        assert state.values.average_discharge_ah() is None
        assert state.values.charged_energy_kwh == pytest.approx(500.0)

    def test_unknown_category_clears_nothing(self):
        state = HistoryState(values=populated_values())
        assert clear_history(state, 99) == state

    def test_cleared_values_reaccumulate(self):
        state = clear_history(HistoryState(values=populated_values()), 3)
        state = stepped(state, voltage_volts=53.0)
        assert state.values.minimum_voltage_volts == pytest.approx(53.0)


class TestHistoryServiceValues:
    def test_discharge_depths_publish_negative(self):
        values = history_service_values(populated_values(), now_wall_seconds=1_003_600.0)
        assert values["/History/DeepestDischarge"] == pytest.approx(-120.0)
        assert values["/History/LastDischarge"] == pytest.approx(-80.0)
        assert values["/History/AverageDischarge"] == pytest.approx(-70.0)

    def test_time_since_last_full_charge_in_seconds(self):
        values = history_service_values(populated_values(), now_wall_seconds=1_003_600.0)
        assert values["/History/TimeSinceLastFullCharge"] == 3600

    def test_fresh_history_publishes_no_records_but_advertises_clearing(self):
        values = history_service_values(HistoryValues(), now_wall_seconds=0.0)
        assert values["/History/DeepestDischarge"] is None
        assert values["/History/AverageDischarge"] is None
        assert values["/History/TimeSinceLastFullCharge"] is None
        assert values["/History/LowVoltageAlarms"] == 0
        assert values["/History/CanBeCleared"] == 1
        assert values["/Settings/HasTemperature"] == 1

    def test_extremes_and_energies(self):
        values = history_service_values(populated_values(), now_wall_seconds=0.0)
        assert values["/History/MinimumVoltage"] == pytest.approx(48.0)
        assert values["/History/MaximumCellVoltage"] == pytest.approx(3.5)
        assert values["/History/MaximumTemperature"] == pytest.approx(35.0)
        assert values["/History/ChargedEnergy"] == pytest.approx(500.0)
        assert values["/History/DischargedEnergy"] == pytest.approx(450.0)


class TestPersistence:
    def test_round_trips_through_the_state_file(self, tmp_path):
        store = StateFile(tmp_path / "state.json")
        store.save(to_persisted(ControlState(), populated_values(), now_wall_seconds=0.0))
        assert StateFile(tmp_path / "state.json").load().history == populated_values()

    def test_state_file_without_history_loads_defaults(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text('{"version": 1, "tripped": [], "charge_stage": "BULK", "cvl_volts": null}')
        assert StateFile(path).load().history == HistoryValues()

    def test_wrong_typed_history_field_is_corrupt(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text(
            '{"version": 1, "tripped": [], "charge_stage": "BULK", "cvl_volts": null,'
            ' "history": {"charged_energy_kwh": "lots"}}'
        )
        with pytest.raises(StateFileError, match="charged_energy_kwh"):
            StateFile(path).load()
        assert (tmp_path / "state.json.corrupt").exists()

    def test_unknown_history_field_is_corrupt_not_a_later_crash(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text(
            '{"version": 1, "tripped": [], "charge_stage": "BULK", "cvl_volts": null, "history": {"no_such_field": 1}}'
        )
        with pytest.raises(StateFileError):
            StateFile(path).load()

    def test_unchanged_history_does_not_rewrite_the_file(self, tmp_path):
        store = StateFile(tmp_path / "state.json")
        assert store.save(to_persisted(ControlState(), populated_values(), now_wall_seconds=0.0)) is True
        assert store.save(to_persisted(ControlState(), populated_values(), now_wall_seconds=1.0)) is False


class TestFloatTransitionEdge:
    def test_step_bank_reports_the_absorption_exit_once(self):
        """The full-charge stamp keys off the decision's FloatTransition entry; the flag must
        fire exactly on the transition step and never while merely staying in the stage."""
        full_packs = make_packs(cell_voltages_volts=(3.61,) * 16, soc_percent=97.0)
        state, decision, _ = step_bank(CONFIG, ControlState(), BankInputs(packs=full_packs, shunt=make_shunt()), now_monotonic=1001.0)
        assert decision.charge_stage is ChargeStage.ABSORPTION
        assert decision.entered_float_transition is False
        hold_expired = 1001.0 + CONFIG.charge_stage.absorption_hold_seconds + 1.0
        inputs = BankInputs(
            packs=make_packs(taken_at=hold_expired, cell_voltages_volts=(3.61,) * 16, soc_percent=97.0),
            shunt=make_shunt(taken_at=hold_expired),
        )
        state, decision, _ = step_bank(CONFIG, state, inputs, now_monotonic=hold_expired)
        assert decision.charge_stage is ChargeStage.FLOAT_TRANSITION
        assert decision.entered_float_transition is True
        state, decision, _ = step_bank(CONFIG, state, inputs, now_monotonic=hold_expired + 1.0)
        assert decision.charge_stage is ChargeStage.FLOAT_TRANSITION
        assert decision.entered_float_transition is False
