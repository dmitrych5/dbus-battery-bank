import pytest

from battery_bank.config import ProtectionConfig, PtcProtectionConfig
from battery_bank.core.interpolation import InterpolationTable
from battery_bank.core.protections import (
    ProtectionState,
    TripKind,
    reset_trips,
    step_protections,
)
from tests.factories import make_snapshot

# Expected aux voltage numerically equals temperature / 10, which makes deviations easy to
# construct: at 25 C the expected voltage is 2.5 V.
PTC_CONFIG = PtcProtectionConfig(
    expected_aux_voltage_by_temperature=InterpolationTable(inputs=(-20.0, 100.0), outputs=(-2.0, 10.0)),
    max_deviation_percent=20.0,
    temperature_sample_interval_seconds=60.0,
    temperature_filter_process_variance=1e-13,
    temperature_sensor_time_constant_minutes=0.0,
)

CONFIG = ProtectionConfig(max_temperature_spread_celsius=10.0, ptc=PTC_CONFIG)


def warmed_up_state(temperature=25.0, samples=15, start_at=1000.0):
    """Runs enough sampling steps for the thermal filter to leave its warmup phase. The shunt
    is not modeled here (shunt_fresh=False), so the aux-presence tracking stays paused."""
    state = ProtectionState()
    now = start_at
    for _ in range(samples):
        result = step_protections(CONFIG, state, [make_snapshot(cell_temperatures_celsius=(temperature,) * 4)], None, False, now)
        state = result.state
        now += PTC_CONFIG.temperature_sample_interval_seconds
    return state, now


class TestTemperatureSpread:
    def test_spread_within_threshold_does_not_trip(self):
        packs = [make_snapshot(cell_temperatures_celsius=(20.0, 25.0, 25.0, 29.0))]
        result = step_protections(CONFIG, ProtectionState(), packs, None, False, 1000.0)
        assert result.zero_limits_required is False
        assert result.temperature_spread_celsius == pytest.approx(9.0)

    def test_spread_across_packs_beyond_threshold_trips(self):
        packs = [
            make_snapshot(unique_id="pack-1", cell_temperatures_celsius=(20.0,) * 4),
            make_snapshot(unique_id="pack-2", cell_temperatures_celsius=(31.0,) * 4),
        ]
        result = step_protections(CONFIG, ProtectionState(), packs, None, False, 1000.0)
        assert result.zero_limits_required is True
        assert result.newly_tripped == (TripKind.TEMPERATURE_SPREAD,)

    def test_trip_latches_after_temperatures_normalize(self):
        packs_hot = [make_snapshot(cell_temperatures_celsius=(20.0, 20.0, 20.0, 31.0))]
        tripped = step_protections(CONFIG, ProtectionState(), packs_hot, None, False, 1000.0)
        packs_normal = [make_snapshot()]
        result = step_protections(CONFIG, tripped.state, packs_normal, None, False, 1001.0)
        assert result.zero_limits_required is True
        assert result.newly_tripped == ()

    def test_disabled_check_never_trips(self):
        config = ProtectionConfig(max_temperature_spread_celsius=None, ptc=None)
        packs = [make_snapshot(cell_temperatures_celsius=(0.0, 50.0, 50.0, 50.0))]
        result = step_protections(config, ProtectionState(), packs, None, False, 1000.0)
        assert result.zero_limits_required is False

    def test_operator_reset_clears_the_latch(self):
        packs_hot = [make_snapshot(cell_temperatures_celsius=(20.0, 20.0, 20.0, 31.0))]
        tripped = step_protections(CONFIG, ProtectionState(), packs_hot, None, False, 1000.0)
        result = step_protections(CONFIG, reset_trips(tripped.state), [make_snapshot()], None, False, 1001.0)
        assert result.zero_limits_required is False


class TestPtcDeviation:
    def test_matching_aux_voltage_does_not_trip(self):
        state, now = warmed_up_state(temperature=25.0)
        result = step_protections(CONFIG, state, [make_snapshot(cell_temperatures_celsius=(25.0,) * 4)], 2.5, True, now)
        assert result.zero_limits_required is False
        assert result.ptc.expected_aux_voltage_volts == pytest.approx(2.5, abs=0.01)
        assert result.ptc.deviation_percent == pytest.approx(0.0, abs=1.0)

    def test_deviation_beyond_threshold_trips_and_latches(self):
        state, now = warmed_up_state(temperature=25.0)
        packs = [make_snapshot(cell_temperatures_celsius=(25.0,) * 4)]
        result = step_protections(CONFIG, state, packs, 3.5, True, now)
        assert result.newly_tripped == (TripKind.PTC_DEVIATION,)
        assert result.ptc.deviation_percent == pytest.approx(40.0, abs=2.0)
        recovered = step_protections(CONFIG, result.state, packs, 2.5, True, now + 1.0)
        assert recovered.zero_limits_required is True

    def test_disconnected_chain_reading_zero_trips(self):
        state, now = warmed_up_state(temperature=25.0)
        result = step_protections(CONFIG, state, [make_snapshot(cell_temperatures_celsius=(25.0,) * 4)], 0.0, True, now)
        assert result.newly_tripped == (TripKind.PTC_DEVIATION,)

    def test_missing_aux_voltage_skips_the_check(self):
        state, now = warmed_up_state(temperature=25.0)
        result = step_protections(CONFIG, state, [make_snapshot(cell_temperatures_celsius=(25.0,) * 4)], None, True, now)
        assert result.zero_limits_required is False
        assert result.ptc.deviation_percent is None
        assert result.ptc.corrected_temperature_celsius == pytest.approx(25.0, abs=0.5)

    def test_rising_temperature_raises_the_corrected_estimate(self):
        config_with_inertia = ProtectionConfig(
            max_temperature_spread_celsius=None,
            ptc=PtcProtectionConfig(
                expected_aux_voltage_by_temperature=PTC_CONFIG.expected_aux_voltage_by_temperature,
                max_deviation_percent=100.0,
                temperature_sample_interval_seconds=60.0,
                temperature_filter_process_variance=1e-6,
                temperature_sensor_time_constant_minutes=480.0,
            ),
        )
        state = ProtectionState()
        now = 1000.0
        temperature = 25.0
        for _ in range(30):
            packs = [make_snapshot(cell_temperatures_celsius=(temperature,) * 4)]
            result = step_protections(config_with_inertia, state, packs, None, False, now)
            state = result.state
            now += 60.0
            temperature += 0.1
        # Sensors rising 0.1 C/min with a 480 min time constant imply the real temperature is
        # far ahead of the sensor readings.
        assert result.ptc.corrected_temperature_celsius > temperature + 10.0


class TestPtcAuxPresence:
    def test_missing_aux_with_fresh_shunt_alarms_after_the_grace(self):
        first = step_protections(CONFIG, ProtectionState(), [make_snapshot()], None, True, 1000.0)
        assert first.state.aux_missing_alarm is False
        later = step_protections(CONFIG, first.state, [make_snapshot()], None, True, 1000.0 + 601.0)
        assert later.state.aux_missing_alarm is True
        # An inoperative protection layer is an alarm, not a latch: limits stay unrestricted.
        assert later.zero_limits_required is False

    def test_aux_returning_clears_the_alarm(self):
        state = ProtectionState(aux_absent_since=0.0, aux_missing_alarm=True)
        result = step_protections(CONFIG, state, [make_snapshot()], 2.0, True, 1000.0)
        assert result.state.aux_missing_alarm is False
        assert result.state.aux_absent_since is None

    def test_stale_shunt_pauses_the_tracking(self):
        result = step_protections(CONFIG, ProtectionState(), [make_snapshot()], None, False, 1000.0)
        assert result.state.aux_absent_since is None
        later = step_protections(CONFIG, result.state, [make_snapshot()], None, False, 9999.0)
        assert later.state.aux_missing_alarm is False

    def test_no_ptc_config_never_alarms(self):
        config = ProtectionConfig(max_temperature_spread_celsius=None, ptc=None)
        result = step_protections(config, ProtectionState(), [make_snapshot()], None, True, 1000.0)
        later = step_protections(config, result.state, [make_snapshot()], None, True, 9999.0)
        assert later.state.aux_missing_alarm is False
