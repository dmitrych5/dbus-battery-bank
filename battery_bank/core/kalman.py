"""Two-state (value, rate) Kalman filter, used to smooth slow temperature sensors and estimate
their rate of change. Pure: each step returns a new state."""

from dataclasses import dataclass


@dataclass(frozen=True)
class KalmanFilterState:
    value_estimate: float = 0.0
    rate_estimate: float = 0.0
    last_time: float | None = None
    p00: float = 100.0
    p01: float = 0.0
    p10: float = 0.0
    p11: float = 100.0


def kalman_step(
    state: KalmanFilterState,
    measurement_variance: float,
    process_variance: float,
    now: float,
    measured_value: float,
    has_new_measurement: bool,
) -> KalmanFilterState:
    if state.last_time is None:
        if has_new_measurement:
            return KalmanFilterState(value_estimate=measured_value, last_time=now)
        return state

    dt = now - state.last_time
    if dt <= 0:
        return state

    # Predict.
    value_predicted = state.value_estimate + state.rate_estimate * dt
    q00 = process_variance * dt**3 / 3.0
    q01 = process_variance * dt**2 / 2.0
    q11 = process_variance * dt
    p00 = state.p00 + dt * (state.p10 + state.p01) + dt**2 * state.p11 + q00
    p01 = state.p01 + dt * state.p11 + q01
    p10 = state.p10 + dt * state.p11 + q01
    p11 = state.p11 + q11

    if not has_new_measurement:
        return KalmanFilterState(
            value_estimate=value_predicted,
            rate_estimate=state.rate_estimate,
            last_time=now,
            p00=p00,
            p01=p01,
            p10=p10,
            p11=p11,
        )

    # Update.
    innovation = measured_value - value_predicted
    innovation_variance = p00 + measurement_variance
    gain0 = p00 / innovation_variance
    gain1 = p10 / innovation_variance
    return KalmanFilterState(
        value_estimate=value_predicted + gain0 * innovation,
        rate_estimate=state.rate_estimate + gain1 * innovation,
        last_time=now,
        p00=(1.0 - gain0) * p00,
        p01=(1.0 - gain0) * p01,
        p10=-gain1 * p00 + p10,
        p11=-gain1 * p01 + p11,
    )
