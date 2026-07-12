"""Bank charge stage machine and charge voltage limit (CVL) control.

One bank-level state machine: the bank is full and balanced only when every pack is, which
reproduces how the previous stack aggregated per-pack charge modes (wait for the last pack
before floating, honor any pack's overvoltage clamp, rebulk only when every pack asks for it).

Stages: Bulk -> Absorption (full and balanced, held for the configured time) ->
FloatTransition (slow ramp down to float voltage) -> Float -> back to Bulk on low SoC.

While in Bulk/Absorption an I-controller lowers CVL whenever the highest cell in the bank
exceeds its setpoint, and raises it back rate-limited, so one runaway cell cannot trip the BMS
overvoltage protection while balancing catches up.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from battery_bank.config import CellVoltageConfig, ChargeStageConfig, CvlControllerConfig
from battery_bank.core.values import BatterySnapshot

FLOAT_TRANSITION_VOLTS_PER_SECOND = 0.001
CVL_RECOVERY_HOLD_SECONDS = 60.0
CVL_RECOVERY_VOLTS_PER_SECOND = 0.001
ABSORPTION_EXIT_VOLTAGE_SAG_VOLTS = 0.5
"""How far a pack's cell-voltage sum must sag below the maximum voltage before absorption falls
back to bulk; sags smaller than this count as measurement noise and load transients."""


class ChargeStage(Enum):
    BULK = "Bulk"
    ABSORPTION = "Absorption"
    FLOAT_TRANSITION = "Float Transition"
    FLOAT = "Float"


@dataclass(frozen=True)
class ChargeStageState:
    stage: ChargeStage = ChargeStage.BULK
    full_and_balanced_since: float | None = None
    """Absorption hold timer; restarts when cells drift out of balance beyond the margin."""
    cvl_volts: float | None = None
    cvl_reduced_at: float | None = None
    """When the I-controller last reduced CVL; recovery is held briefly after a reduction."""
    float_transition_started_at: float | None = None
    float_transition_start_cvl_volts: float | None = None
    last_step_at: float | None = None


@dataclass(frozen=True)
class ChargeStageResult:
    state: ChargeStageState
    stage: ChargeStage
    cvl_volts: float
    entered_float_transition: bool
    """True on the step where the bank switched from absorption to float transition; triggers
    the per-pack SoC reset and the full-charge history timestamp."""
    cvl_reduced_by_controller: bool


def step_charge_stage(
    cell_voltage: CellVoltageConfig,
    stage_config: ChargeStageConfig,
    controller: CvlControllerConfig,
    cells_per_pack: int,
    packs: Sequence[BatterySnapshot],
    state: ChargeStageState,
    now_monotonic: float,
) -> ChargeStageResult:
    max_voltage = cells_per_pack * cell_voltage.max_volts
    float_voltage = cells_per_pack * cell_voltage.float_volts

    pack_cell_sums = [sum(pack.cell_voltages_volts) for pack in packs]
    pack_cell_diffs = [pack.max_cell_voltage_volts() - pack.min_cell_voltage_volts() for pack in packs]
    bank_full = all(cell_sum >= max_voltage for cell_sum in pack_cell_sums)
    bank_balanced = all(diff <= stage_config.balanced_cell_diff_volts for diff in pack_cell_diffs)
    any_pack_sagged = any(cell_sum < max_voltage - ABSORPTION_EXIT_VOLTAGE_SAG_VOLTS for cell_sum in pack_cell_sums)
    any_pack_unbalanced_beyond_margin = any(
        diff > stage_config.balanced_cell_diff_volts + stage_config.balanced_cell_diff_restart_margin_volts for diff in pack_cell_diffs
    )
    every_pack_below_rebulk_soc = all(pack.soc_percent < stage_config.rebulk_soc_percent for pack in packs)

    stage = state.stage
    hold_timer = state.full_and_balanced_since
    transition_started_at = state.float_transition_started_at
    transition_start_cvl = state.float_transition_start_cvl_volts
    entered_float_transition = False

    if stage is ChargeStage.BULK:
        if bank_full and bank_balanced:
            stage = ChargeStage.ABSORPTION
            hold_timer = now_monotonic
    elif stage is ChargeStage.ABSORPTION:
        if any_pack_sagged:
            stage = ChargeStage.BULK
            hold_timer = None
        elif any_pack_unbalanced_beyond_margin:
            hold_timer = now_monotonic
        elif now_monotonic - hold_timer > stage_config.absorption_hold_seconds:
            stage = ChargeStage.FLOAT_TRANSITION
            hold_timer = None
            entered_float_transition = True
            transition_started_at = now_monotonic
            transition_start_cvl = state.cvl_volts if state.cvl_volts is not None else max_voltage
    elif every_pack_below_rebulk_soc:  # FLOAT_TRANSITION or FLOAT
        stage = ChargeStage.BULK
        transition_started_at = None
        transition_start_cvl = None

    if stage in (ChargeStage.BULK, ChargeStage.ABSORPTION):
        cvl, cvl_reduced_at = _step_cvl_controller(cell_voltage, controller, cells_per_pack, packs, state, now_monotonic, max_voltage)
    elif stage is ChargeStage.FLOAT_TRANSITION:
        ramped_down = transition_start_cvl - FLOAT_TRANSITION_VOLTS_PER_SECOND * (now_monotonic - transition_started_at)
        cvl = max(ramped_down, float_voltage)
        if cvl == float_voltage:
            stage = ChargeStage.FLOAT
            transition_started_at = None
            transition_start_cvl = None
        cvl_reduced_at = None
    else:  # FLOAT
        cvl = float_voltage
        cvl_reduced_at = None

    new_state = ChargeStageState(
        stage=stage,
        full_and_balanced_since=hold_timer,
        cvl_volts=cvl,
        cvl_reduced_at=cvl_reduced_at,
        float_transition_started_at=transition_started_at,
        float_transition_start_cvl_volts=transition_start_cvl,
        last_step_at=now_monotonic,
    )
    return ChargeStageResult(
        state=new_state,
        stage=stage,
        cvl_volts=cvl,
        entered_float_transition=entered_float_transition,
        cvl_reduced_by_controller=stage in (ChargeStage.BULK, ChargeStage.ABSORPTION) and cvl < max_voltage,
    )


def _step_cvl_controller(
    cell_voltage: CellVoltageConfig,
    controller: CvlControllerConfig,
    cells_per_pack: int,
    packs: Sequence[BatterySnapshot],
    state: ChargeStageState,
    now_monotonic: float,
    max_voltage: float,
) -> tuple[float, float | None]:
    """One I-controller step towards keeping the bank's highest cell at its setpoint.

    Reductions apply immediately; recovery is held for CVL_RECOVERY_HOLD_SECONDS after the last
    reduction and then rate-limited, so the charger does not slam back into an unbalanced cell.
    Returns the new CVL and the new time-of-last-reduction.
    """
    min_voltage = cells_per_pack * cell_voltage.min_volts
    previous_cvl = state.cvl_volts if state.cvl_volts is not None else max_voltage
    elapsed = now_monotonic - state.last_step_at if state.last_step_at is not None else 0.0

    highest_cell = max(pack.max_cell_voltage_volts() for pack in packs)
    overshoot = highest_cell - (cell_voltage.max_volts + controller.setpoint_margin_volts)
    target_cvl = previous_cvl - overshoot * controller.volts_per_volt_second * elapsed
    target_cvl = min(max(target_cvl, min_voltage), max_voltage)

    if target_cvl < previous_cvl:
        return target_cvl, now_monotonic

    reduced_at = state.cvl_reduced_at
    if reduced_at is None:
        cvl = target_cvl
    else:
        # Only the part of this step falling after the hold window counts towards the ramp.
        ramp_seconds = min(elapsed, now_monotonic - (reduced_at + CVL_RECOVERY_HOLD_SECONDS))
        if ramp_seconds <= 0:
            cvl = previous_cvl
        else:
            cvl = min(target_cvl, previous_cvl + CVL_RECOVERY_VOLTS_PER_SECOND * ramp_seconds)
    if cvl >= max_voltage:
        reduced_at = None
    return cvl, reduced_at
