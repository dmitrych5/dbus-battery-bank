from pathlib import Path

import pytest

from battery_bank.config import load_config
from battery_bank.core.bank import BankInputs, ControlState, step_bank
from battery_bank.core.charge_stage import ChargeStage, ChargeStageState
from battery_bank.core.protections import ProtectionState, TripKind
from battery_bank.persistence.state_file import (
    PersistedState,
    StateFile,
    StateFileError,
    restore_control_state,
    to_persisted,
)
from tests.test_bank import healthy_inputs, make_packs, make_shunt

CONFIG = load_config(Path(__file__).parent.parent / "config.example.ini")


class TestStateFile:
    def test_round_trip(self, tmp_path):
        state = PersistedState(tripped=frozenset({TripKind.PTC_DEVIATION}), charge_stage=ChargeStage.FLOAT, cvl_volts=53.2)
        store = StateFile(tmp_path / "state.json")
        store.save(state)
        assert StateFile(tmp_path / "state.json").load() == state

    def test_missing_file_loads_defaults(self, tmp_path):
        assert StateFile(tmp_path / "state.json").load() == PersistedState()

    def test_identical_state_is_not_rewritten(self, tmp_path):
        path = tmp_path / "state.json"
        store = StateFile(path)
        store.save(PersistedState())
        path.write_text("externally changed")
        store.save(PersistedState())
        assert path.read_text() == "externally changed"

    def test_changed_state_is_rewritten(self, tmp_path):
        path = tmp_path / "state.json"
        store = StateFile(path)
        store.save(PersistedState())
        store.save(PersistedState(charge_stage=ChargeStage.FLOAT))
        assert StateFile(path).load().charge_stage is ChargeStage.FLOAT

    def test_corrupt_file_raises_and_is_quarantined(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text('{"version": 1, "tripped": ["NO_SUCH_TRIP"], "charge_stage": "BULK", "cvl_volts": null}')
        with pytest.raises(StateFileError, match="corrupt state file"):
            StateFile(path).load()
        assert not path.exists()
        assert (tmp_path / "state.json.corrupt").exists()
        # The next start is not blocked.
        assert StateFile(path).load() == PersistedState()

    def test_unsupported_version_raises(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text('{"version": 99, "tripped": [], "charge_stage": "BULK", "cvl_volts": null}')
        with pytest.raises(StateFileError):
            StateFile(path).load()

    def test_wrong_typed_cvl_is_corrupt_not_a_later_crash(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text('{"version": 1, "tripped": [], "charge_stage": "BULK", "cvl_volts": "56.0"}')
        with pytest.raises(StateFileError, match="cvl_volts"):
            StateFile(path).load()
        assert (tmp_path / "state.json.corrupt").exists()

    def test_wrong_typed_thermal_field_is_corrupt(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text(
            '{"version": 1, "tripped": [], "charge_stage": "BULK", "cvl_volts": null,'
            ' "thermal": {"value_estimate": "hot", "rate_estimate": 0, "updates_count": 1, "saved_at_wall_seconds": 0}}'
        )
        with pytest.raises(StateFileError, match="value_estimate"):
            StateFile(path).load()
        assert (tmp_path / "state.json.corrupt").exists()


class TestRestore:
    def test_trips_survive_a_restart_and_keep_limits_at_zero(self):
        persisted = PersistedState(tripped=frozenset({TripKind.PTC_DEVIATION}))
        restored = restore_control_state(persisted, CONFIG, now_monotonic=500.0, now_wall_seconds=0.0)
        _, decision, _ = step_bank(CONFIG, restored, healthy_inputs(), now_monotonic=1001.0)
        assert decision.ready is True
        assert decision.ccl_amps == 0.0
        assert decision.protections.zero_limits_required is True

    def test_absorption_hold_restarts_after_restore(self):
        persisted = PersistedState(charge_stage=ChargeStage.ABSORPTION, cvl_volts=57.6)
        restored = restore_control_state(persisted, CONFIG, now_monotonic=1000.0, now_wall_seconds=0.0)
        full_packs = make_packs(cell_voltages_volts=(3.61,) * 16, soc_percent=97.0)
        _, decision, _ = step_bank(CONFIG, restored, BankInputs(packs=full_packs, shunt=make_shunt()), now_monotonic=1001.0)
        assert decision.charge_stage is ChargeStage.ABSORPTION
        state, decision, _ = step_bank(
            CONFIG,
            restored,
            BankInputs(packs=make_packs(taken_at=1121.5, cell_voltages_volts=(3.61,) * 16, soc_percent=97.0), shunt=make_shunt(taken_at=1121.5)),
            now_monotonic=1122.0,
        )
        assert decision.charge_stage is ChargeStage.FLOAT_TRANSITION

    def test_float_transition_resumes_ramping_from_persisted_cvl(self):
        persisted = PersistedState(charge_stage=ChargeStage.FLOAT_TRANSITION, cvl_volts=55.0)
        restored = restore_control_state(persisted, CONFIG, now_monotonic=1000.0, now_wall_seconds=0.0)
        inputs = BankInputs(packs=make_packs(taken_at=1100.0, soc_percent=97.0), shunt=make_shunt(taken_at=1100.0))
        _, decision, _ = step_bank(CONFIG, restored, inputs, now_monotonic=1100.0)
        assert decision.charge_stage is ChargeStage.FLOAT_TRANSITION
        assert decision.cvl_volts == pytest.approx(55.0 - 0.001 * 100.0)

    def test_reduced_cvl_gets_a_fresh_recovery_hold(self):
        persisted = PersistedState(charge_stage=ChargeStage.BULK, cvl_volts=57.0)
        restored = restore_control_state(persisted, CONFIG, now_monotonic=1000.0, now_wall_seconds=0.0)
        assert restored.charge_stage.cvl_reduced_at == 1000.0

    def test_to_persisted_round_trips_through_control_state(self):
        state, _, _ = step_bank(CONFIG, ControlState(), healthy_inputs(), now_monotonic=1001.0)
        persisted = to_persisted(state, now_wall_seconds=5000.0)
        assert persisted.charge_stage is ChargeStage.BULK
        assert persisted.cvl_volts == pytest.approx(57.6)
        assert persisted.tripped == frozenset()

    def test_cvl_persists_floored_to_the_quantum(self):
        state = ControlState(charge_stage=ChargeStageState(stage=ChargeStage.FLOAT_TRANSITION, cvl_volts=55.749))
        assert to_persisted(state, now_wall_seconds=0.0).cvl_volts == pytest.approx(55.70)

    def test_cvl_exactly_on_a_quantum_is_not_floored_down(self):
        state = ControlState(charge_stage=ChargeStageState(stage=ChargeStage.FLOAT_TRANSITION, cvl_volts=57.6))
        assert to_persisted(state, now_wall_seconds=0.0).cvl_volts == pytest.approx(57.6)

    def test_cvl_ramp_within_one_quantum_does_not_rewrite_the_file(self, tmp_path):
        """The float-transition ramp changes the CVL every control cycle; flash writes must be
        bounded to quantum crossings, not happen per cycle."""

        def state_at(cvl: float) -> ControlState:
            return ControlState(charge_stage=ChargeStageState(stage=ChargeStage.FLOAT_TRANSITION, cvl_volts=cvl))

        store = StateFile(tmp_path / "state.json")
        assert store.save(to_persisted(state_at(55.749), now_wall_seconds=0.0)) is True
        assert store.save(to_persisted(state_at(55.748), now_wall_seconds=1.0)) is False
        assert store.save(to_persisted(state_at(55.701), now_wall_seconds=2.0)) is False
        assert store.save(to_persisted(state_at(55.699), now_wall_seconds=3.0)) is True


class TestThermalRestore:
    def persisted_thermal(self, value=25.0, rate=1e-4, saved_at=1000.0):
        from battery_bank.persistence.state_file import PersistedThermalState

        return PersistedState(thermal=PersistedThermalState(value_estimate=value, rate_estimate=rate, updates_count=15, saved_at_wall_seconds=saved_at))

    def test_thermal_state_round_trips_through_the_file(self, tmp_path):
        state = self.persisted_thermal()
        store = StateFile(tmp_path / "state.json")
        store.save(state)
        assert StateFile(tmp_path / "state.json").load() == state

    def test_restore_advances_value_by_rate_and_keeps_the_rate(self):
        two_hours = 7200.0
        restored = restore_control_state(self.persisted_thermal(), CONFIG, now_monotonic=100.0, now_wall_seconds=1000.0 + two_hours)
        assert restored.protections.thermal.kalman.value_estimate == pytest.approx(25.0 + 1e-4 * two_hours)
        assert restored.protections.thermal.kalman.rate_estimate == pytest.approx(1e-4)
        assert restored.protections.thermal.updates_count == 15

    def test_inertia_correction_is_active_immediately_after_restore(self):
        # A realistic restart: the persisted value matches what the slow sensors still read.
        restored = restore_control_state(self.persisted_thermal(value=20.0), CONFIG, now_monotonic=1000.0, now_wall_seconds=1060.0)
        _, decision, _ = step_bank(CONFIG, restored, healthy_inputs(), now_monotonic=1001.0)
        # Packs read 20 C; the persisted rate of 1e-4 C/s with the 480 min sensor time constant
        # puts the corrected estimate about 2.9 C above the readings, with no warmup blackout.
        assert decision.protections.ptc.corrected_temperature_celsius == pytest.approx(20.0 + 2.88, abs=0.5)

    def test_snapshot_older_than_the_max_age_starts_cold(self):
        restored = restore_control_state(self.persisted_thermal(saved_at=0.0), CONFIG, now_monotonic=100.0, now_wall_seconds=7 * 3600.0)
        assert restored.protections.thermal.updates_count == 0
        assert restored.protections.thermal.kalman.last_time is None

    def test_to_persisted_snapshots_a_warmed_filter(self):
        state, _, _ = step_bank(CONFIG, ControlState(), healthy_inputs(), now_monotonic=1001.0)
        persisted = to_persisted(state, now_wall_seconds=5000.0)
        assert persisted.thermal is not None
        assert persisted.thermal.updates_count == 1
        assert persisted.thermal.saved_at_wall_seconds == 5000.0

    def test_to_persisted_omits_a_cold_filter(self):
        assert to_persisted(ControlState(), now_wall_seconds=5000.0).thermal is None
