from pathlib import Path

from battery_bank.config import load_config
from battery_bank.core.bank import BankInputs, ControlState, step_bank
from battery_bank.core.values import ChainAggregatedLimits
from battery_bank.publishing.diagnostics_text import diagnostics_values, pack_diagnostics_values
from tests.factories import make_snapshot
from tests.test_bank import healthy_inputs, make_packs, make_shunt, ready_state

CONFIG = load_config(Path(__file__).parent.parent / "config.example.ini")


def rendered(inputs=None, state=None, now=1001.0):
    inputs = inputs if inputs is not None else healthy_inputs()
    new_state, decision, _ = step_bank(CONFIG, state if state is not None else ControlState(), inputs, now_monotonic=now)
    return diagnostics_values(CONFIG, new_state, decision, inputs.packs, inputs.shunt, now), new_state


class TestDiagnosticsText:
    def test_overview_shows_the_control_essentials(self):
        values, _ = rendered()
        overview = values["/Info/ChargeModeDebug"]
        assert "stage: Bulk • CVL: 57.600 V" in overview
        assert "CCL: 30.0 A • DCL: 750.0 A" in overview
        assert "SoC: 82.5% (shunt)" in overview
        assert "packs fresh: 3/3 • shunt fresh: yes" in overview
        assert "PTC: aux 0.777 V" in overview
        assert "trips: none" in overview

    def test_overview_names_the_highest_cell(self):
        packs = make_packs()[:2] + (
            make_snapshot(unique_id="pack-3", address=3, cell_voltages_volts=(3.3,) * 15 + (3.35,)),
        )
        values, _ = rendered(BankInputs(packs=packs, shunt=make_shunt()))
        assert "highest cell: 3.350 V (pack-3 C16)" in values["/Info/ChargeModeDebug"]

    def test_float_requirements_show_per_pack_progress_and_hold(self):
        full_packs = make_packs(cell_voltages_volts=(3.61,) * 16, soc_percent=97.0)
        values, state = rendered(BankInputs(packs=full_packs, shunt=make_shunt()))
        later_inputs = BankInputs(packs=make_packs(taken_at=1050.0, cell_voltages_volts=(3.61,) * 16, soc_percent=97.0), shunt=make_shunt(taken_at=1050.0))
        values, _ = rendered(later_inputs, state=state, now=1051.0)
        float_text = values["/Info/ChargeModeDebugFloat"]
        assert "pack-1: sum 57.76/57.60 V" in float_text
        assert "hold: 50/120 s" in float_text

    def test_float_requirements_before_the_hold_starts(self):
        values, _ = rendered()
        assert "hold: not started" in values["/Info/ChargeModeDebugFloat"]

    def test_rebulk_requirements_compare_each_pack_to_the_threshold(self):
        values, _ = rendered()
        bulk_text = values["/Info/ChargeModeDebugBulk"]
        assert "every pack SoC below 96%" in bulk_text
        assert "(only applies in float)" in bulk_text
        assert "pack-1: SoC 80.0% < 96%" in bulk_text


class TestPackDiagnosticsText:
    def pack_debug(self, inputs=None, pack_index=0, now=1001.0):
        inputs = inputs if inputs is not None else healthy_inputs()
        _, decision, _ = step_bank(CONFIG, ControlState(), inputs, now_monotonic=now)
        return pack_diagnostics_values(decision, inputs.packs[pack_index], now)["/Info/ChargeModeDebug"]

    def test_shows_the_packs_limit_contributions_with_sources(self):
        text = self.pack_debug()
        assert "CCL: 10.0 A (Max current)" in text
        assert "DCL: 250.0 A (Max current)" in text

    def test_shows_bms_and_fet_state_and_data_age(self):
        text = self.pack_debug()
        assert "BMS limits: charge 200 A • discharge 300 A • CVL 55.20 V" in text
        assert "FETs: charge on • discharge on • balancing: 0 cells" in text
        assert "data age: 1.0 s" in text

    def test_chain_master_shows_the_aggregated_limits(self):
        master = make_snapshot(chain_aggregated_limits=ChainAggregatedLimits(30.0, 750.0))
        packs = (master,) + make_packs()[1:]
        text = self.pack_debug(BankInputs(packs=packs, shunt=make_shunt()))
        assert "chain limits (master): charge 30 A • discharge 750 A" in text
        assert "chain limits" not in self.pack_debug(BankInputs(packs=packs, shunt=make_shunt()), pack_index=1)

    def test_stale_picture_shows_no_limit_detail(self):
        inputs = BankInputs(packs=make_packs()[:2], shunt=make_shunt())
        _, decision, _ = step_bank(CONFIG, ready_state(), inputs, now_monotonic=1001.0)
        text = pack_diagnostics_values(decision, inputs.packs[0], 1001.0)["/Info/ChargeModeDebug"]
        assert "CCL: n/a (no fresh bank decision)" in text

    def test_disabled_fets_stand_out(self):
        pack = make_snapshot(charge_fet_enabled=False)
        _, decision, _ = step_bank(CONFIG, ControlState(), healthy_inputs(), now_monotonic=1001.0)
        text = pack_diagnostics_values(decision, pack, 1001.0)["/Info/ChargeModeDebug"]
        assert "charge OFF" in text
