import dataclasses
from pathlib import Path

import pytest

from battery_bank.config import load_config
from battery_bank.core.bank import BankInputs, ControlState, step_bank
from battery_bank.core.charge_stage import ChargeStage
from battery_bank.core.history import (
    HistoryState,
    HistoryValues,
    bank_history_sample,
    clear_history,
    pack_history_sample,
    step_history,
)
from battery_bank.core.values import AlarmSeverity
from battery_bank.persistence.state_file import StateFile, StateFileError, to_persisted
from battery_bank.publishing.service_values import history_service_values, pack_history_service_values
from tests.factories import make_alarms, make_snapshot
from tests.test_bank import healthy_inputs, make_packs, make_shunt, make_totals

CONFIG = load_config(Path(__file__).parent.parent / "config.example.ini")


def make_decision(**overrides):
    """A healthy bank decision with selected fields overridden, so each test states only the
    inputs the accumulator should react to."""
    _, decision, _ = step_bank(CONFIG, ControlState(), healthy_inputs(), now_monotonic=1001.0)
    return dataclasses.replace(decision, **overrides) if overrides else decision


def bank_stepped(state=HistoryState(), wall=5_000_000.0, packs=(), **decision_overrides):
    return step_history(state, bank_history_sample(make_decision(**decision_overrides), packs), wall)


def pack_stepped(state=HistoryState(), wall=5_000_000.0, **snapshot_overrides):
    return step_history(state, pack_history_sample(make_snapshot(**snapshot_overrides)), wall)


class TestBankSample:
    def test_cell_and_temperature_extremes_span_all_packs(self):
        packs = (
            make_snapshot(cell_voltages_volts=(3.1,) + (3.3,) * 15, cell_temperatures_celsius=(5.0, 20.0)),
            make_snapshot(unique_id="pack-2", cell_voltages_volts=(3.3,) * 15 + (3.45,), cell_temperatures_celsius=(21.0, 39.0)),
        )
        state = bank_stepped(packs=packs)
        assert state.values.minimum_cell_voltage_volts == pytest.approx(3.1)
        assert state.values.maximum_cell_voltage_volts == pytest.approx(3.45)
        assert state.values.minimum_temperature_celsius == pytest.approx(5.0)
        assert state.values.maximum_temperature_celsius == pytest.approx(39.0)

    def test_bank_does_not_track_voltage_extremes(self):
        """On the aggregate the min/max voltage paths carry the shunt's lifetime records."""
        state = bank_stepped(packs=make_packs())
        assert state.values.minimum_voltage_volts is None

    def test_no_packs_leave_extremes_untouched(self):
        state = bank_stepped(packs=())
        assert state.values.minimum_cell_voltage_volts is None

    def test_extremes_only_ratchet(self):
        state = bank_stepped(packs=make_packs(cell_voltages_volts=(3.2,) * 16))
        state = bank_stepped(state, packs=make_packs(cell_voltages_volts=(3.3,) * 16))
        assert state.values.minimum_cell_voltage_volts == pytest.approx(3.2)
        assert state.values.maximum_cell_voltage_volts == pytest.approx(3.3)

    def test_full_charge_stamps_the_wall_clock(self):
        state = bank_stepped(entered_float_transition=True, wall=6_000_000.0)
        assert state.values.last_full_charge_at_wall_seconds == pytest.approx(6_000_000.0)
        state = bank_stepped(state, wall=6_000_100.0)
        assert state.values.last_full_charge_at_wall_seconds == pytest.approx(6_000_000.0)


class TestPackSample:
    def test_tracks_the_packs_own_extremes(self):
        state = pack_stepped(voltage_volts=52.0, cell_voltages_volts=(3.2,) + (3.3,) * 15, cell_temperatures_celsius=(18.0, 22.0))
        state = pack_stepped(state, voltage_volts=54.0, cell_voltages_volts=(3.3,) * 15 + (3.4,), cell_temperatures_celsius=(19.0, 30.0))
        assert state.values.minimum_voltage_volts == pytest.approx(52.0)
        assert state.values.maximum_voltage_volts == pytest.approx(54.0)
        assert state.values.minimum_cell_voltage_volts == pytest.approx(3.2)
        assert state.values.maximum_cell_voltage_volts == pytest.approx(3.4)
        assert state.values.minimum_temperature_celsius == pytest.approx(18.0)
        assert state.values.maximum_temperature_celsius == pytest.approx(30.0)

    def test_pack_alarm_edges_count_per_pack(self):
        state = pack_stepped()
        state = pack_stepped(state, alarms=make_alarms(low_cell_voltage=AlarmSeverity.WARNING))
        state = pack_stepped(state, alarms=make_alarms(low_cell_voltage=AlarmSeverity.WARNING))
        assert state.values.low_voltage_alarm_count == 1

    def test_pack_never_stamps_a_full_charge(self):
        state = pack_stepped()
        assert state.values.last_full_charge_at_wall_seconds is None


class TestVoltageAlarmCounts:
    def test_rising_edge_counts_once_until_cleared(self):
        state = bank_stepped()
        state = bank_stepped(state, alarms=make_alarms(low_voltage=AlarmSeverity.ALARM))
        state = bank_stepped(state, alarms=make_alarms(low_voltage=AlarmSeverity.ALARM))
        assert state.values.low_voltage_alarm_count == 1
        state = bank_stepped(state)
        state = bank_stepped(state, alarms=make_alarms(low_cell_voltage=AlarmSeverity.WARNING))
        assert state.values.low_voltage_alarm_count == 2

    def test_alarm_already_active_at_start_is_not_counted(self):
        """After a restart the pre-restart edge was already counted (or lost with its crash);
        adopting the active state without counting avoids double counting."""
        state = bank_stepped(alarms=make_alarms(high_voltage=AlarmSeverity.ALARM))
        assert state.values.high_voltage_alarm_count == 0
        state = bank_stepped(state, alarms=make_alarms(high_voltage=AlarmSeverity.ALARM))
        assert state.values.high_voltage_alarm_count == 0

    def test_cell_and_pack_flags_share_one_counter(self):
        state = bank_stepped()
        state = bank_stepped(state, alarms=make_alarms(high_voltage=AlarmSeverity.ALARM, high_cell_voltage=AlarmSeverity.ALARM))
        assert state.values.high_voltage_alarm_count == 1


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
        last_full_charge_at_wall_seconds=1_000_000.0,
    )


class TestClear:
    def test_clear_resets_everything(self):
        state = clear_history(HistoryState(values=populated_values()))
        assert state.values == HistoryValues()

    def test_cleared_values_reaccumulate(self):
        state = clear_history(HistoryState(values=populated_values()))
        state = pack_stepped(state, voltage_volts=53.0)
        assert state.values.minimum_voltage_volts == pytest.approx(53.0)


class TestAggregateHistoryServiceValues:
    def test_shunt_lifetime_history_publishes_raw(self):
        values = history_service_values(populated_values(), make_totals(), now_wall_seconds=0.0)
        assert values["/History/DeepestDischarge"] == pytest.approx(-120.0)
        assert values["/History/LastDischarge"] == pytest.approx(-80.0)
        assert values["/History/AverageDischarge"] == pytest.approx(-70.0)
        assert values["/History/ChargeCycles"] == 45
        assert values["/History/FullDischarges"] == 1
        assert values["/History/TotalAhDrawn"] == pytest.approx(-52_000.0)
        assert values["/History/MinimumVoltage"] == pytest.approx(47.5)
        assert values["/History/MaximumVoltage"] == pytest.approx(56.1)
        assert values["/History/AutomaticSyncs"] == 250
        assert values["/History/DischargedEnergy"] == pytest.approx(1400.0)
        assert values["/History/ChargedEnergy"] == pytest.approx(1500.0)

    def test_shunt_paths_unknown_before_the_history_frame(self):
        values = history_service_values(populated_values(), None, now_wall_seconds=0.0)
        assert values["/History/DeepestDischarge"] is None
        assert values["/History/ChargeCycles"] is None
        assert values["/History/ChargedEnergy"] is None

    def test_driver_values_and_time_since_full_charge(self):
        values = history_service_values(populated_values(), make_totals(), now_wall_seconds=1_003_600.0)
        assert values["/History/MinimumCellVoltage"] == pytest.approx(3.0)
        assert values["/History/MaximumCellVoltage"] == pytest.approx(3.5)
        assert values["/History/MinimumTemperature"] == pytest.approx(4.0)
        assert values["/History/MaximumTemperature"] == pytest.approx(35.0)
        assert values["/History/LowVoltageAlarms"] == 2
        assert values["/History/HighVoltageAlarms"] == 1
        assert values["/History/TimeSinceLastFullCharge"] == 3600
        assert values["/History/CanBeCleared"] == 1
        assert values["/Settings/HasTemperature"] == 1

    def test_fresh_history_hides_records_but_advertises_clearing(self):
        values = history_service_values(HistoryValues(), None, now_wall_seconds=0.0)
        assert values["/History/MinimumCellVoltage"] is None
        assert values["/History/TimeSinceLastFullCharge"] is None
        assert values["/History/LowVoltageAlarms"] == 0
        assert values["/History/CanBeCleared"] == 1


class TestPackHistoryServiceValues:
    def test_publishes_the_packs_driver_history(self):
        values = pack_history_service_values(populated_values())
        assert values["/History/MinimumVoltage"] == pytest.approx(48.0)
        assert values["/History/MaximumVoltage"] == pytest.approx(56.0)
        assert values["/History/MinimumCellVoltage"] == pytest.approx(3.0)
        assert values["/History/MaximumTemperature"] == pytest.approx(35.0)
        assert values["/History/LowVoltageAlarms"] == 2
        assert values["/History/CanBeCleared"] == 1
        assert values["/Settings/HasTemperature"] == 1

    def test_no_bank_level_paths(self):
        values = pack_history_service_values(populated_values())
        assert "/History/TimeSinceLastFullCharge" not in values
        assert "/History/ChargeCycles" not in values
        assert "/History/DeepestDischarge" not in values


class TestPersistence:
    def test_bank_and_pack_history_round_trip_through_the_state_file(self, tmp_path):
        store = StateFile(tmp_path / "state.json")
        pack_history = {"pack-1": populated_values(), "pack-2": HistoryValues(low_voltage_alarm_count=7)}
        store.save(to_persisted(ControlState(), populated_values(), pack_history, now_wall_seconds=0.0))
        loaded = StateFile(tmp_path / "state.json").load()
        assert loaded.history == populated_values()
        assert loaded.pack_history == pack_history

    def test_state_file_without_history_loads_defaults(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text('{"version": 1, "tripped": [], "charge_stage": "BULK", "cvl_volts": null}')
        assert StateFile(path).load().history == HistoryValues()
        assert StateFile(path).load().pack_history == {}

    def test_fields_dropped_by_a_schema_change_are_ignored_not_corrupt(self, tmp_path):
        """Quarantining the file over removed informational fields would discard the safety
        latches persisted next to them."""
        path = tmp_path / "state.json"
        path.write_text(
            '{"version": 1, "tripped": ["PTC_DEVIATION"], "charge_stage": "BULK", "cvl_volts": null,'
            ' "history": {"charged_energy_kwh": 12.5, "low_voltage_alarm_count": 3}}'
        )
        loaded = StateFile(path).load()
        assert loaded.history.low_voltage_alarm_count == 3
        assert len(loaded.tripped) == 1

    def test_wrong_typed_history_field_is_corrupt(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text(
            '{"version": 1, "tripped": [], "charge_stage": "BULK", "cvl_volts": null,'
            ' "history": {"low_voltage_alarm_count": "many"}}'
        )
        with pytest.raises(StateFileError, match="low_voltage_alarm_count"):
            StateFile(path).load()
        assert (tmp_path / "state.json.corrupt").exists()

    def test_wrong_typed_pack_history_is_corrupt(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text(
            '{"version": 1, "tripped": [], "charge_stage": "BULK", "cvl_volts": null, "pack_history": {"pack-1": []}}'
        )
        with pytest.raises(StateFileError):
            StateFile(path).load()

    def test_unchanged_history_does_not_rewrite_the_file(self, tmp_path):
        store = StateFile(tmp_path / "state.json")
        assert store.save(to_persisted(ControlState(), populated_values(), {"pack-1": HistoryValues()}, now_wall_seconds=0.0)) is True
        assert store.save(to_persisted(ControlState(), populated_values(), {"pack-1": HistoryValues()}, now_wall_seconds=1.0)) is False


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
