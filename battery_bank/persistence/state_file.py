"""Persists the control state that must survive restarts: latched protection trips (a crash
must never clear a safety response), the charge stage with its CVL (so a restart mid-cycle
does not re-run absorption on a full battery or jump the voltage), and the accumulated
lifetime history (whose write cadence the main loop limits, like the thermal filter's).

Monotonic timers inside ChargeStageState are meaningless across restarts, so only the stage
and CVL are persisted; restore_control_state() rebases the timers conservatively — the
absorption hold restarts, a float-transition ramp resumes from the persisted CVL, and a
controller-reduced CVL gets a fresh recovery hold.

Writes are atomic (temp file + rename) and skipped when nothing changed, to spare the GX
device's flash.
"""

import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path

from battery_bank.config import Config
from battery_bank.core.bank import ControlState
from battery_bank.core.charge_stage import ChargeStage, ChargeStageState
from battery_bank.core.history import HISTORY_FIELD_NAMES, HistoryValues
from battery_bank.core.protections import ProtectionState, ThermalInertiaState, TripKind, restored_thermal_state

STATE_FILE_VERSION = 1

CVL_PERSIST_QUANTUM_VOLTS = 0.05
"""The CVL ramps continuously (float transition, controller recovery), so persisting it exactly
would rewrite the flash every control cycle for the whole ramp. Flooring to this quantum bounds
writes to one per quantum crossed, and flooring errs low — a slightly lower restored CVL is
always the safe direction. A reduction smaller than one quantum restores as unreduced, which
the fresh recovery hold on restore absorbs."""


class StateFileError(Exception):
    """The state file exists but cannot be trusted. The caller must fail loud: latched trips
    may be lost, which the operator has to know about."""


@dataclass(frozen=True)
class PersistedThermalState:
    """A snapshot of the PTC thermal-inertia filter. Even a few-hours-old snapshot restores
    the slowly-learned rate estimate, which would otherwise take the filter tens of minutes to
    re-learn after every restart — leaving the overheat protection under-corrected meanwhile."""

    value_estimate: float
    rate_estimate: float
    updates_count: int
    saved_at_wall_seconds: float
    """Wall clock, not monotonic: the downtime across a restart must be measurable."""


@dataclass(frozen=True)
class PersistedState:
    tripped: frozenset[TripKind] = frozenset()
    charge_stage: ChargeStage = ChargeStage.BULK
    cvl_volts: float | None = None
    thermal: PersistedThermalState | None = None
    history: HistoryValues = HistoryValues()
    """The bank's driver-computed history. The main loop cadence-limits how often a fresh
    snapshot is handed in — the same treatment as the thermal filter."""
    pack_history: dict[str, HistoryValues] = field(default_factory=dict)
    """Per-pack driver-computed history, keyed by the pack's unique id; held to the same
    cadence as the bank history."""


def to_persisted(
    state: ControlState, history: HistoryValues, pack_history: dict[str, HistoryValues], now_wall_seconds: float
) -> PersistedState:
    thermal = state.protections.thermal
    return PersistedState(
        tripped=state.protections.tripped,
        charge_stage=state.charge_stage.stage,
        cvl_volts=_quantized_cvl(state.charge_stage.cvl_volts),
        history=history,
        pack_history=dict(sorted(pack_history.items())),
        thermal=(
            PersistedThermalState(
                value_estimate=thermal.kalman.value_estimate,
                rate_estimate=thermal.kalman.rate_estimate,
                updates_count=thermal.updates_count,
                saved_at_wall_seconds=now_wall_seconds,
            )
            if thermal.updates_count > 0
            else None
        ),
    )


def _quantized_cvl(cvl_volts: float | None) -> float | None:
    if cvl_volts is None:
        return None
    # The epsilon keeps an exactly-on-quantum value from flooring one step down on float error.
    return round(math.floor(cvl_volts / CVL_PERSIST_QUANTUM_VOLTS + 1e-9) * CVL_PERSIST_QUANTUM_VOLTS, 2)


def restore_control_state(persisted: PersistedState, config: Config, now_monotonic: float, now_wall_seconds: float) -> ControlState:
    stage = persisted.charge_stage
    cvl = persisted.cvl_volts
    max_voltage = config.cells_per_pack * config.cell_voltage.max_volts
    charge_stage = ChargeStageState(
        stage=stage,
        full_and_balanced_since=now_monotonic if stage is ChargeStage.ABSORPTION else None,
        cvl_volts=cvl,
        cvl_reduced_at=(now_monotonic if stage in (ChargeStage.BULK, ChargeStage.ABSORPTION) and cvl is not None and cvl < max_voltage else None),
        float_transition_started_at=now_monotonic if stage is ChargeStage.FLOAT_TRANSITION else None,
        float_transition_start_cvl_volts=cvl if stage is ChargeStage.FLOAT_TRANSITION else None,
        last_step_at=None,
    )
    thermal = (
        restored_thermal_state(
            value_estimate=persisted.thermal.value_estimate,
            rate_estimate=persisted.thermal.rate_estimate,
            updates_count=persisted.thermal.updates_count,
            age_seconds=now_wall_seconds - persisted.thermal.saved_at_wall_seconds,
            now_monotonic=now_monotonic,
        )
        if persisted.thermal is not None
        else ThermalInertiaState()
    )
    return ControlState(charge_stage=charge_stage, protections=ProtectionState(tripped=persisted.tripped, thermal=thermal))


def _validated_number(name: str, value: object, optional: bool = False) -> float | None:
    """Wrong-typed values must fail here, where the corrupt file is quarantined — not cycles
    later inside the control core, which would crash-loop on the same file forever."""
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number: got {value!r}")
    return value


def _validated_thermal(data: object) -> PersistedThermalState | None:
    if data is None:
        return None
    thermal = PersistedThermalState(**data)
    for name in ("value_estimate", "rate_estimate", "updates_count", "saved_at_wall_seconds"):
        _validated_number(f"thermal.{name}", getattr(thermal, name))
    return thermal


_HISTORY_OPTIONAL_FIELDS = frozenset(
    name for name in HISTORY_FIELD_NAMES if getattr(HistoryValues(), name) is None
)
"""Fields that legitimately persist as null (no extreme or event recorded yet); the counters
and integrals always hold a number."""


def _validated_history(data: object) -> HistoryValues:
    if data is None:
        # State files written before history existed.
        return HistoryValues()
    if not isinstance(data, dict):
        raise ValueError(f"history must be a mapping: got {data!r}")
    # Fields dropped by a schema change are ignored rather than treated as corruption:
    # quarantining the whole file over informational history would discard the safety latches
    # persisted next to it.
    history = HistoryValues(**{name: value for name, value in data.items() if name in HISTORY_FIELD_NAMES})
    for name in HISTORY_FIELD_NAMES:
        _validated_number(f"history.{name}", getattr(history, name), optional=name in _HISTORY_OPTIONAL_FIELDS)
    return history


def _validated_pack_history(data: object) -> dict[str, HistoryValues]:
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"pack_history must be a mapping: got {data!r}")
    return {unique_id: _validated_history(entry) for unique_id, entry in data.items()}


def _history_json(values: HistoryValues) -> dict[str, object]:
    return {name: getattr(values, name) for name in HISTORY_FIELD_NAMES}


class StateFile:
    def __init__(self, path: Path):
        self._path = path
        self._last_saved: str | None = None

    def load(self) -> PersistedState:
        """Returns the persisted state, or defaults when no file exists yet. Raises
        StateFileError on a corrupt or incomprehensible file, after moving it aside for
        inspection so the next start is not blocked."""
        try:
            text = self._path.read_text()
        except FileNotFoundError:
            return PersistedState()
        except OSError as error:
            raise StateFileError(f"cannot read state file {self._path}: {error}") from error
        try:
            data = json.loads(text)
            if data["version"] != STATE_FILE_VERSION:
                raise ValueError(f"unsupported state file version {data['version']}")
            state = PersistedState(
                tripped=frozenset(TripKind[name] for name in data["tripped"]),
                charge_stage=ChargeStage[data["charge_stage"]],
                cvl_volts=_validated_number("cvl_volts", data["cvl_volts"], optional=True),
                thermal=_validated_thermal(data.get("thermal")),
                history=_validated_history(data.get("history")),
                pack_history=_validated_pack_history(data.get("pack_history")),
            )
        except (ValueError, KeyError, TypeError) as error:
            quarantine_path = self._path.with_suffix(self._path.suffix + ".corrupt")
            os.replace(self._path, quarantine_path)
            raise StateFileError(f"corrupt state file moved to {quarantine_path}: {error!r}") from error
        self._last_saved = text
        return state

    def save(self, state: PersistedState) -> bool:
        """Returns True when the file was actually (re)written."""
        serialized = json.dumps(
            {
                "version": STATE_FILE_VERSION,
                "tripped": sorted(kind.name for kind in state.tripped),
                "charge_stage": state.charge_stage.name,
                "cvl_volts": state.cvl_volts,
                "thermal": vars(state.thermal) if state.thermal is not None else None,
                "history": _history_json(state.history),
                "pack_history": {unique_id: _history_json(values) for unique_id, values in state.pack_history.items()},
            }
        )
        if serialized == self._last_saved:
            return False
        temporary_path = self._path.with_suffix(self._path.suffix + ".new")
        with temporary_path.open("w") as file:
            file.write(serialized)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, self._path)
        self._last_saved = serialized
        return True
