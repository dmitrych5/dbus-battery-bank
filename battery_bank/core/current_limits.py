"""Charge and discharge current limit (CCL/DCL) calculation.

Pure functions: callers provide snapshots, previous state, configuration, and the current time.
The per-pack limit is the minimum over all limiting sources; the bank limit is the lowest
per-pack limit times the pack count, passed through the update policy (rate limiting and
zero-recovery hysteresis).
"""

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from battery_bank.config import CurrentLimitConfig, LimitUpdatePolicyConfig
from battery_bank.core.values import BatterySnapshot


class CurrentDirection(Enum):
    CHARGE = "charge"
    DISCHARGE = "discharge"


class LimitSource(Enum):
    """Where a current limit comes from. Values appear in the GUI limitation text; the GUI
    highlights temperatures by matching 'temperature' in that text."""

    MAX_CURRENT = "Max current"
    BMS = "BMS"
    CHAIN_MASTER = "Chain master"
    FET_OFF = "FET off"
    CELL_VOLTAGE = "Cell voltage"
    CELL_TEMPERATURE = "Cell temperature"
    AMBIENT_TEMPERATURE = "Ambient temperature"
    MOSFET_TEMPERATURE = "MOSFET temperature"


@dataclass(frozen=True)
class LimitContribution:
    """One source's cap on a pack's current, for diagnostics and the GUI limitation text."""

    source: LimitSource
    amps: float


@dataclass(frozen=True)
class PackCurrentLimit:
    pack_unique_id: str
    amps: float
    active_sources: tuple[LimitSource, ...]
    contributions: tuple[LimitContribution, ...]


@dataclass(frozen=True)
class CurrentLimitState:
    published_amps: float | None = None
    published_at_monotonic: float | None = None


@dataclass(frozen=True)
class BankCurrentLimit:
    state: CurrentLimitState
    published_amps: float
    candidate_amps: float
    """The freshly calculated limit; differs from published_amps while the update policy holds
    the previous value."""
    limiting_pack_unique_id: str
    active_sources: tuple[LimitSource, ...]
    held_at_zero: bool
    """True while zero-recovery hysteresis keeps the published limit at zero although the
    calculated limit has recovered slightly above it."""
    per_pack: tuple[PackCurrentLimit, ...]


def compute_bank_current_limit(
    limit_config: CurrentLimitConfig,
    update_policy: LimitUpdatePolicyConfig,
    direction: CurrentDirection,
    packs: Sequence[BatterySnapshot],
    state: CurrentLimitState,
    now_monotonic: float,
) -> BankCurrentLimit:
    """Packs must be non-empty; the caller decides separately what stale or missing packs mean."""
    chain_limit_amps_by_port = {
        pack.identity.port: _chain_amps(pack, direction) for pack in packs if pack.chain_aggregated_limits is not None
    }
    pack_limits = tuple(
        compute_pack_current_limit(limit_config, direction, pack, chain_limit_amps_by_port.get(pack.identity.port)) for pack in packs
    )
    lowest = min(pack_limits, key=lambda pack_limit: pack_limit.amps)
    candidate_amps = lowest.amps * len(packs)

    zero_recovery_min_amps = limit_config.zero_recovery_min_fraction * limit_config.max_amps * len(packs)
    new_state, held_at_zero = _apply_update_policy(candidate_amps, state, update_policy, zero_recovery_min_amps, now_monotonic)

    return BankCurrentLimit(
        state=new_state,
        published_amps=new_state.published_amps,
        candidate_amps=candidate_amps,
        limiting_pack_unique_id=lowest.pack_unique_id,
        active_sources=lowest.active_sources,
        held_at_zero=held_at_zero,
        per_pack=pack_limits,
    )


def compute_pack_current_limit(
    limit_config: CurrentLimitConfig,
    direction: CurrentDirection,
    pack: BatterySnapshot,
    chain_limit_amps: float | None,
) -> PackCurrentLimit:
    contributions = [
        LimitContribution(LimitSource.MAX_CURRENT, limit_config.max_amps),
        LimitContribution(LimitSource.BMS, _bms_amps(pack, direction)),
        LimitContribution(
            LimitSource.CELL_VOLTAGE,
            limit_config.max_amps * limit_config.cell_voltage_curve.fraction_at(_cell_voltage_extreme(pack, direction)),
        ),
        LimitContribution(
            LimitSource.CELL_TEMPERATURE,
            limit_config.max_amps * _worst_fraction_at_extremes(limit_config.cell_temperature_curve, pack.cell_temperatures_celsius),
        ),
        LimitContribution(
            LimitSource.AMBIENT_TEMPERATURE,
            limit_config.max_amps * limit_config.ambient_temperature_curve.fraction_at(pack.ambient_temperature_celsius),
        ),
        LimitContribution(
            LimitSource.MOSFET_TEMPERATURE,
            limit_config.max_amps * limit_config.mosfet_temperature_curve.fraction_at(pack.mosfet_temperature_celsius),
        ),
    ]
    if chain_limit_amps is not None:
        contributions.append(LimitContribution(LimitSource.CHAIN_MASTER, chain_limit_amps))
    if not _fet_enabled(pack, direction):
        contributions.append(LimitContribution(LimitSource.FET_OFF, 0.0))

    amps = min(contribution.amps for contribution in contributions)
    # A source is only "active" when it genuinely restricts below the configured maximum;
    # curves at fraction 1.0 and limits equal to the maximum are not restricting anything.
    active_sources = tuple(
        contribution.source for contribution in contributions if contribution.amps == amps and contribution.amps < limit_config.max_amps
    ) or (LimitSource.MAX_CURRENT,)
    return PackCurrentLimit(
        pack_unique_id=pack.identity.unique_id,
        amps=amps,
        active_sources=active_sources,
        contributions=tuple(contributions),
    )


def _apply_update_policy(
    candidate_amps: float,
    state: CurrentLimitState,
    policy: LimitUpdatePolicyConfig,
    zero_recovery_min_amps: float,
    now_monotonic: float,
) -> tuple[CurrentLimitState, bool]:
    """Decides whether to publish the candidate now or keep the previously published value.

    Publishes when: nothing was published yet, the minimum update interval elapsed, the change
    exceeds the immediate-update fraction, or the candidate dropped to zero. A recovery from
    zero below the zero-recovery threshold is held at zero to prevent flapping.
    """
    if state.published_amps is None:
        return CurrentLimitState(candidate_amps, now_monotonic), False

    interval_elapsed = now_monotonic - state.published_at_monotonic >= policy.min_update_interval_seconds
    change_exceeds_fraction = abs(state.published_amps - candidate_amps) >= state.published_amps * policy.immediate_update_change_fraction
    dropped_to_zero = candidate_amps == 0.0 and state.published_amps != 0.0
    if not (interval_elapsed or change_exceeds_fraction or dropped_to_zero):
        return state, False

    recovering_from_zero = state.published_amps == 0.0 and candidate_amps > 0.0
    if recovering_from_zero and candidate_amps < zero_recovery_min_amps:
        return CurrentLimitState(0.0, now_monotonic), True
    return CurrentLimitState(candidate_amps, now_monotonic), False


def _worst_fraction_at_extremes(curve, values: Sequence[float]) -> float:
    """Evaluates the curve at the lowest and the highest value; the lower fraction wins, so a
    single sensor at either extreme limits the whole pack."""
    return min(curve.fraction_at(min(values)), curve.fraction_at(max(values)))


def _bms_amps(pack: BatterySnapshot, direction: CurrentDirection) -> float:
    limits = pack.bms_limits
    return limits.charge_current_amps if direction is CurrentDirection.CHARGE else limits.discharge_current_amps


def _chain_amps(pack: BatterySnapshot, direction: CurrentDirection) -> float:
    limits = pack.chain_aggregated_limits
    return limits.charge_current_amps if direction is CurrentDirection.CHARGE else limits.discharge_current_amps


def _fet_enabled(pack: BatterySnapshot, direction: CurrentDirection) -> bool:
    return pack.charge_fet_enabled if direction is CurrentDirection.CHARGE else pack.discharge_fet_enabled


def _cell_voltage_extreme(pack: BatterySnapshot, direction: CurrentDirection) -> float:
    """Charging is limited by the highest cell, discharging by the lowest."""
    return pack.max_cell_voltage_volts() if direction is CurrentDirection.CHARGE else pack.min_cell_voltage_volts()
