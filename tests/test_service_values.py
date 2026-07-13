from pathlib import Path

import pytest

from battery_bank.config import load_config
from battery_bank.core.bank import BankInputs, ControlState, step_bank
from battery_bank.publishing.service_values import aggregate_service_values, pack_service_values
from tests.factories import make_snapshot
from tests.test_bank import healthy_inputs, make_packs, make_shunt, ready_state

CONFIG = load_config(Path(__file__).parent.parent / "config.example.ini")


def healthy_decision(inputs=None):
    inputs = inputs if inputs is not None else healthy_inputs()
    _, decision, _ = step_bank(CONFIG, ControlState(), inputs, now_monotonic=1001.0)
    return decision, inputs


class TestAggregateServiceValues:
    def test_control_and_measurement_paths(self):
        decision, inputs = healthy_decision()
        values = aggregate_service_values(CONFIG, decision, inputs.packs, inputs.shunt)
        assert values["/Info/MaxChargeVoltage"] == pytest.approx(57.6 + 0.05)
        assert values["/Info/MaxChargeCurrent"] == pytest.approx(30.0)
        assert values["/Info/MaxDischargeCurrent"] == pytest.approx(750.0)
        assert values["/Info/ChargeMode"] == "Bulk"
        assert values["/Soc"] == pytest.approx(82.5)
        assert values["/Dc/0/Voltage"] == pytest.approx(53.0)
        assert values["/InstalledCapacity"] == pytest.approx(300.0)
        assert values["/System/NrOfModulesOnline"] == 3
        assert values["/System/NrOfModulesOffline"] == 0
        assert values["/Io/AllowToCharge"] == 1
        assert values["/Alarms/BmsCable"] == 0

    def test_air_temperature_is_the_hottest_ambient_sensor(self):
        packs = make_packs()[:2] + (make_snapshot(unique_id="pack-3", address=3, ambient_temperature_celsius=31.5),)
        decision, _ = healthy_decision(BankInputs(packs=packs, shunt=make_shunt()))
        values = aggregate_service_values(CONFIG, decision, packs, make_shunt())
        assert values["/AirTemperature"] == pytest.approx(31.5)

    def test_cell_extremes_name_the_pack_and_cell(self):
        packs = make_packs()[:2] + (
            make_snapshot(unique_id="pack-3", address=3, cell_voltages_volts=(3.3,) * 15 + (3.35,)),
        )
        decision, _ = healthy_decision(BankInputs(packs=packs, shunt=make_shunt()))
        values = aggregate_service_values(CONFIG, decision, packs, make_shunt())
        assert values["/System/MaxCellVoltage"] == pytest.approx(3.35)
        assert values["/System/MaxVoltageCellId"] == "pack-3 C16"

    def test_vrm_workaround_paths_carry_ptc_diagnostics(self):
        decision, inputs = healthy_decision()
        values = aggregate_service_values(CONFIG, decision, inputs.packs, inputs.shunt)
        assert values["/Dc/1/Voltage"] == pytest.approx(7.77)
        assert values["/History/MinimumStarterVoltage"] == pytest.approx(20.0)
        assert values["/Dc/0/MidVoltageDeviation"] is not None

    def test_stale_pack_data_shows_in_limitation_text_and_modules(self):
        inputs = BankInputs(packs=make_packs()[:2], shunt=make_shunt())
        _, decision, _ = step_bank(CONFIG, ready_state(), inputs, now_monotonic=1001.0)
        values = aggregate_service_values(CONFIG, decision, inputs.packs, inputs.shunt)
        assert values["/Info/ChargeLimitation"] == "Stale battery data"
        assert values["/System/NrOfModulesOffline"] == 1
        assert values["/Alarms/BmsCable"] == 2
        assert values["/Info/MaxChargeCurrent"] == 0.0

    def test_limitation_text_names_the_restricting_source(self):
        packs = make_packs(ambient_temperature_celsius=45.0)
        decision, _ = healthy_decision(BankInputs(packs=packs, shunt=make_shunt()))
        values = aggregate_service_values(CONFIG, decision, packs, make_shunt())
        assert values["/Info/ChargeLimitation"] == "Ambient temperature"

    def test_time_to_go_only_while_discharging(self):
        decision, inputs = healthy_decision()
        values = aggregate_service_values(CONFIG, decision, inputs.packs, inputs.shunt)
        # 240 Ah remaining across the bank at 5 A discharge.
        assert values["/TimeToGo"] == int(240.0 / 5.0 * 3600)
        charging = BankInputs(packs=make_packs(), shunt=make_shunt(current_amps=5.0))
        decision, _ = healthy_decision(charging)
        assert aggregate_service_values(CONFIG, decision, charging.packs, charging.shunt)["/TimeToGo"] is None


class TestPackServiceValues:
    def test_measurement_and_cell_paths(self):
        decision, inputs = healthy_decision()
        values = pack_service_values(decision, inputs.packs[0])
        assert values["/Soc"] == pytest.approx(80.0)
        assert values["/Voltages/Cell1"] == pytest.approx(3.3)
        assert values["/Voltages/Sum"] == pytest.approx(52.8)
        assert values["/System/Temperature4"] == pytest.approx(20.0)
        assert values["/AirTemperature"] == pytest.approx(25.0)
        assert values["/ConsumedAmphours"] == pytest.approx(-20.0)
        assert values["/History/TotalAhDrawn"] == pytest.approx(1000.0)
        assert values["/Io/AllowToCharge"] == 1

    def test_per_pack_limits_and_limitation(self):
        packs = make_packs()[:2] + (make_snapshot(unique_id="pack-3", address=3, ambient_temperature_celsius=45.0),)
        inputs = BankInputs(packs=packs, shunt=make_shunt())
        decision, _ = healthy_decision(inputs)
        limited = pack_service_values(decision, packs[2])
        assert limited["/Info/MaxChargeCurrent"] == pytest.approx(5.0)
        assert limited["/Info/ChargeLimitation"] == "Ambient temperature"
        unlimited = pack_service_values(decision, packs[0])
        assert unlimited["/Info/MaxChargeCurrent"] == pytest.approx(10.0)
        assert unlimited["/Info/ChargeLimitation"] == "Max current"

    def test_alarm_paths_from_pack_flags(self):
        from tests.factories import make_alarms
        from battery_bank.core.values import AlarmSeverity

        pack = make_snapshot(alarms=make_alarms(internal_failure=AlarmSeverity.ALARM))
        decision, _ = healthy_decision()
        values = pack_service_values(decision, pack)
        assert values["/Alarms/InternalFailure"] == 2
        assert values["/Alarms/LowSoc"] == 0


class TestServiceInternalAlarm:
    def test_internal_failure_alarm_raised_for_service_faults(self):
        decision, inputs = healthy_decision()
        values = aggregate_service_values(CONFIG, decision, inputs.packs, inputs.shunt, service_internal_alarm=True)
        assert values["/Alarms/InternalFailure"] == 2
        values = aggregate_service_values(CONFIG, decision, inputs.packs, inputs.shunt)
        assert values["/Alarms/InternalFailure"] == 0
