import pytest

from battery_bank.config import CellVoltageConfig, ChargeStageConfig, CvlControllerConfig
from battery_bank.core.charge_stage import (
    ChargeStage,
    ChargeStageState,
    step_charge_stage,
)
from tests.factories import make_snapshot

CELLS_PER_PACK = 16
CELL_VOLTAGE = CellVoltageConfig(min_volts=2.77, max_volts=3.60, float_volts=3.325)
STAGE_CONFIG = ChargeStageConfig(
    absorption_hold_seconds=120.0,
    balanced_cell_diff_volts=0.020,
    balanced_cell_diff_restart_margin_volts=0.003,
    rebulk_soc_percent=96.0,
    cvl_charger_offset_volts=0.05,
)
CONTROLLER = CvlControllerConfig(volts_per_volt_second=0.2, setpoint_margin_volts=0.020)

MAX_VOLTAGE = CELLS_PER_PACK * CELL_VOLTAGE.max_volts
FLOAT_VOLTAGE = CELLS_PER_PACK * CELL_VOLTAGE.float_volts


def pack(base_cell_volts=3.30, highest_cell_volts=None, soc=80.0, unique_id="pack-1"):
    highest = highest_cell_volts if highest_cell_volts is not None else base_cell_volts
    return make_snapshot(unique_id=unique_id, cell_voltages_volts=(base_cell_volts,) * 15 + (highest,), soc_percent=soc)


FULL_BALANCED_PACK_KWARGS = dict(base_cell_volts=3.61, soc=97.0)


class Bank:
    """Drives the stage machine step by step with an advancing clock."""

    def __init__(self, start_at=1000.0):
        self.state = ChargeStageState()
        self.now = start_at
        self.result = None

    def step(self, packs, advance_seconds=1.0):
        self.now += advance_seconds
        self.result = step_charge_stage(CELL_VOLTAGE, STAGE_CONFIG, CONTROLLER, CELLS_PER_PACK, packs, self.state, self.now)
        self.state = self.result.state
        return self.result


class TestStageTransitions:
    def test_starts_in_bulk_at_max_voltage(self):
        result = Bank().step([pack()])
        assert result.stage is ChargeStage.BULK
        assert result.cvl_volts == pytest.approx(MAX_VOLTAGE)

    def test_full_and_balanced_bank_enters_absorption(self):
        result = Bank().step([pack(**FULL_BALANCED_PACK_KWARGS)])
        assert result.stage is ChargeStage.ABSORPTION

    def test_stays_in_bulk_until_every_pack_is_full(self):
        result = Bank().step([pack(**FULL_BALANCED_PACK_KWARGS, unique_id="pack-1"), pack(base_cell_volts=3.55, unique_id="pack-2")])
        assert result.stage is ChargeStage.BULK

    def test_stays_in_bulk_while_unbalanced(self):
        result = Bank().step([pack(base_cell_volts=3.61, highest_cell_volts=3.64)])
        assert result.stage is ChargeStage.BULK

    def test_absorption_hold_leads_to_float_transition(self):
        bank = Bank()
        bank.step([pack(**FULL_BALANCED_PACK_KWARGS)])
        result = bank.step([pack(**FULL_BALANCED_PACK_KWARGS)], advance_seconds=121.0)
        assert result.stage is ChargeStage.FLOAT_TRANSITION
        assert result.entered_float_transition is True

    def test_unbalance_beyond_margin_restarts_the_hold(self):
        bank = Bank()
        bank.step([pack(**FULL_BALANCED_PACK_KWARGS)])
        bank.step([pack(base_cell_volts=3.61, highest_cell_volts=3.634, soc=97.0)], advance_seconds=100.0)
        result = bank.step([pack(**FULL_BALANCED_PACK_KWARGS)], advance_seconds=100.0)
        assert result.stage is ChargeStage.ABSORPTION
        result = bank.step([pack(**FULL_BALANCED_PACK_KWARGS)], advance_seconds=21.0)
        assert result.stage is ChargeStage.FLOAT_TRANSITION

    def test_unbalance_within_margin_does_not_restart_the_hold(self):
        bank = Bank()
        bank.step([pack(**FULL_BALANCED_PACK_KWARGS)])
        bank.step([pack(base_cell_volts=3.61, highest_cell_volts=3.632, soc=97.0)], advance_seconds=100.0)
        result = bank.step([pack(**FULL_BALANCED_PACK_KWARGS)], advance_seconds=21.0)
        assert result.stage is ChargeStage.FLOAT_TRANSITION

    def test_voltage_sag_falls_back_to_bulk(self):
        bank = Bank()
        bank.step([pack(**FULL_BALANCED_PACK_KWARGS)])
        result = bank.step([pack(base_cell_volts=3.56, soc=97.0)])
        assert result.stage is ChargeStage.BULK
        assert result.entered_float_transition is False

    def test_float_transition_ramps_down_to_float(self):
        bank = Bank()
        bank.step([pack(**FULL_BALANCED_PACK_KWARGS)])
        bank.step([pack(**FULL_BALANCED_PACK_KWARGS)], advance_seconds=121.0)
        result = bank.step([pack(**FULL_BALANCED_PACK_KWARGS)], advance_seconds=100.0)
        assert result.stage is ChargeStage.FLOAT_TRANSITION
        assert result.cvl_volts == pytest.approx(MAX_VOLTAGE - 0.1)
        result = bank.step([pack(**FULL_BALANCED_PACK_KWARGS)], advance_seconds=(MAX_VOLTAGE - FLOAT_VOLTAGE) / 0.001)
        assert result.stage is ChargeStage.FLOAT
        assert result.cvl_volts == pytest.approx(FLOAT_VOLTAGE)

    def test_rebulk_when_every_pack_drops_below_threshold(self):
        bank = self.bank_in_float()
        result = bank.step([pack(soc=95.0, unique_id="pack-1"), pack(soc=95.5, unique_id="pack-2")])
        assert result.stage is ChargeStage.BULK

    def test_no_rebulk_while_any_pack_is_above_threshold(self):
        bank = self.bank_in_float()
        result = bank.step([pack(soc=95.0, unique_id="pack-1"), pack(soc=97.0, unique_id="pack-2")])
        assert result.stage is ChargeStage.FLOAT

    def bank_in_float(self):
        bank = Bank()
        packs = [pack(**FULL_BALANCED_PACK_KWARGS, unique_id="pack-1"), pack(**FULL_BALANCED_PACK_KWARGS, unique_id="pack-2")]
        bank.step(packs)
        bank.step(packs, advance_seconds=121.0)
        bank.step(packs, advance_seconds=(MAX_VOLTAGE - FLOAT_VOLTAGE) / 0.001 + 1.0)
        assert bank.result.stage is ChargeStage.FLOAT
        return bank


class TestCvlController:
    def test_cell_overshoot_reduces_cvl_proportionally(self):
        bank = Bank()
        bank.step([pack()])
        result = bank.step([pack(highest_cell_volts=3.68)])
        # Overshoot above setpoint (3.60 + 0.020) is 0.06 V; gain 0.2 V/Vs over 1 s.
        assert result.cvl_volts == pytest.approx(MAX_VOLTAGE - 0.012)
        assert result.cvl_reduced_by_controller is True

    def test_reduction_accumulates_while_overshoot_persists(self):
        bank = Bank()
        bank.step([pack()])
        bank.step([pack(highest_cell_volts=3.68)])
        result = bank.step([pack(highest_cell_volts=3.68)])
        assert result.cvl_volts == pytest.approx(MAX_VOLTAGE - 0.024)

    def test_recovery_is_held_after_a_reduction(self):
        bank = Bank()
        bank.step([pack(highest_cell_volts=3.68)])
        reduced = bank.step([pack(highest_cell_volts=3.68)]).cvl_volts
        result = bank.step([pack(highest_cell_volts=3.40)], advance_seconds=30.0)
        assert result.cvl_volts == pytest.approx(reduced)

    def test_recovery_after_hold_is_rate_limited(self):
        bank = Bank()
        bank.step([pack(highest_cell_volts=3.68)])
        reduced = bank.step([pack(highest_cell_volts=3.68)]).cvl_volts
        # A 70 s step spans the 60 s hold window; only the 10 s after it count towards the ramp.
        result = bank.step([pack(highest_cell_volts=3.40)], advance_seconds=70.0)
        assert result.cvl_volts == pytest.approx(reduced + 0.001 * 10.0)
        result = bank.step([pack(highest_cell_volts=3.40)], advance_seconds=1.0)
        assert result.cvl_volts == pytest.approx(reduced + 0.001 * 11.0)

    def test_recovery_stops_at_max_voltage(self):
        bank = Bank()
        bank.step([pack(highest_cell_volts=3.63)])
        bank.step([pack(highest_cell_volts=3.63)])
        result = bank.step([pack(highest_cell_volts=3.40)], advance_seconds=10000.0)
        assert result.cvl_volts == pytest.approx(MAX_VOLTAGE)
        assert result.cvl_reduced_by_controller is False

    def test_cvl_never_falls_below_minimum_voltage(self):
        bank = Bank()
        bank.step([pack()])
        result = bank.step([pack(highest_cell_volts=3.9)], advance_seconds=100000.0)
        assert result.cvl_volts == pytest.approx(CELLS_PER_PACK * CELL_VOLTAGE.min_volts)

    def test_highest_cell_across_the_bank_drives_the_controller(self):
        bank = Bank()
        bank.step([pack(unique_id="pack-1"), pack(unique_id="pack-2")])
        result = bank.step([pack(unique_id="pack-1"), pack(highest_cell_volts=3.68, unique_id="pack-2")])
        assert result.cvl_volts == pytest.approx(MAX_VOLTAGE - 0.012)
