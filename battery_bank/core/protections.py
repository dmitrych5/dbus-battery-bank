"""Bank-level protections that latch CCL and DCL to zero until an operator reset.

Latched trips survive restarts (the outer loop persists ProtectionState), so a crash can never
silently clear a safety response. Diagnostics keep being produced while tripped, so the
operator can watch the condition on VRM before resetting.
"""

from dataclasses import dataclass
from enum import Enum
from statistics import mean
from typing import Sequence

from battery_bank.config import ProtectionConfig, PtcProtectionConfig
from battery_bank.core.kalman import KalmanFilterState, kalman_step
from battery_bank.core.values import BatterySnapshot

KALMAN_MEASUREMENT_VARIANCE = 0.005
THERMAL_FILTER_WARMUP_UPDATES = 10
"""The rate estimate is too noisy at first; inertia correction stays off until this many
temperature samples were absorbed."""


class TripKind(Enum):
    TEMPERATURE_SPREAD = "temperature spread across the bank too high"
    PTC_DEVIATION = "PTC aux voltage deviates from the expected value"


@dataclass(frozen=True)
class ThermalInertiaState:
    kalman: KalmanFilterState = KalmanFilterState()
    updates_count: int = 0
    last_sample_at: float | None = None


@dataclass(frozen=True)
class ProtectionState:
    tripped: frozenset[TripKind] = frozenset()
    thermal: ThermalInertiaState = ThermalInertiaState()


@dataclass(frozen=True)
class PtcDiagnostics:
    corrected_temperature_celsius: float
    expected_aux_voltage_volts: float | None
    deviation_percent: float | None
    """Expected voltage and deviation are None while no aux voltage reading is available."""


@dataclass(frozen=True)
class ProtectionsResult:
    state: ProtectionState
    zero_limits_required: bool
    newly_tripped: tuple[TripKind, ...]
    temperature_spread_celsius: float | None
    ptc: PtcDiagnostics | None


def reset_trips(state: ProtectionState) -> ProtectionState:
    """Operator-initiated reset; the only way a latched trip clears."""
    return ProtectionState(tripped=frozenset(), thermal=state.thermal)


def step_protections(
    config: ProtectionConfig,
    state: ProtectionState,
    packs: Sequence[BatterySnapshot],
    aux_voltage_volts: float | None,
    now_monotonic: float,
) -> ProtectionsResult:
    tripped = set(state.tripped)
    newly_tripped: list[TripKind] = []

    temperature_spread = _temperature_spread_celsius(packs)
    if (
        config.max_temperature_spread_celsius is not None
        and temperature_spread is not None
        and temperature_spread > config.max_temperature_spread_celsius
        and TripKind.TEMPERATURE_SPREAD not in tripped
    ):
        tripped.add(TripKind.TEMPERATURE_SPREAD)
        newly_tripped.append(TripKind.TEMPERATURE_SPREAD)

    thermal = state.thermal
    ptc_diagnostics = None
    if config.ptc is not None and packs:
        bank_temperature = mean(mean(pack.cell_temperatures_celsius) for pack in packs)
        thermal, corrected_temperature = _correct_thermal_inertia(config.ptc, thermal, bank_temperature, now_monotonic)
        ptc_diagnostics = _check_ptc_deviation(config.ptc, corrected_temperature, aux_voltage_volts)
        if (
            ptc_diagnostics.deviation_percent is not None
            and ptc_diagnostics.deviation_percent > config.ptc.max_deviation_percent
            and TripKind.PTC_DEVIATION not in tripped
        ):
            tripped.add(TripKind.PTC_DEVIATION)
            newly_tripped.append(TripKind.PTC_DEVIATION)

    return ProtectionsResult(
        state=ProtectionState(tripped=frozenset(tripped), thermal=thermal),
        zero_limits_required=bool(tripped),
        newly_tripped=tuple(newly_tripped),
        temperature_spread_celsius=temperature_spread,
        ptc=ptc_diagnostics,
    )


def _temperature_spread_celsius(packs: Sequence[BatterySnapshot]) -> float | None:
    all_temperatures = [temperature for pack in packs for temperature in pack.cell_temperatures_celsius]
    if not all_temperatures:
        return None
    return max(all_temperatures) - min(all_temperatures)


def _correct_thermal_inertia(
    ptc_config: PtcProtectionConfig,
    thermal: ThermalInertiaState,
    measured_temperature: float,
    now_monotonic: float,
) -> tuple[ThermalInertiaState, float]:
    """Estimates the actual current temperature ahead of the slow sensors: the smoothed reading
    plus its rate of change times the sensor time constant."""
    has_new_sample = thermal.last_sample_at is None or now_monotonic - thermal.last_sample_at >= ptc_config.temperature_sample_interval_seconds
    kalman = kalman_step(
        thermal.kalman,
        KALMAN_MEASUREMENT_VARIANCE,
        ptc_config.temperature_filter_process_variance,
        now_monotonic,
        measured_temperature,
        has_new_sample,
    )
    new_thermal = ThermalInertiaState(
        kalman=kalman,
        updates_count=thermal.updates_count + (1 if has_new_sample else 0),
        last_sample_at=now_monotonic if has_new_sample else thermal.last_sample_at,
    )
    if new_thermal.updates_count < THERMAL_FILTER_WARMUP_UPDATES:
        return new_thermal, kalman.value_estimate
    corrected = kalman.value_estimate + ptc_config.temperature_sensor_time_constant_minutes * 60.0 * kalman.rate_estimate
    return new_thermal, corrected


def _check_ptc_deviation(
    ptc_config: PtcProtectionConfig,
    corrected_temperature: float,
    aux_voltage_volts: float | None,
) -> PtcDiagnostics:
    if aux_voltage_volts is None:
        return PtcDiagnostics(corrected_temperature, expected_aux_voltage_volts=None, deviation_percent=None)
    expected = ptc_config.expected_aux_voltage_by_temperature.value_at(corrected_temperature)
    deviation_percent = 100.0 * abs(aux_voltage_volts - expected) / expected
    return PtcDiagnostics(corrected_temperature, expected, deviation_percent)
