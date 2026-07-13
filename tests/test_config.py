from pathlib import Path

import pytest

from battery_bank.config import BatteryPortConfig, ConfigError, load_config

EXAMPLE_CONFIG_PATH = Path(__file__).parent.parent / "config.example.ini"


def config_text_with(replacements: dict[str, str] | None = None, removals: tuple[str, ...] = ()) -> str:
    """The example config with whole lines replaced or removed, matched by line prefix."""
    replacements = replacements or {}
    lines = []
    for line in EXAMPLE_CONFIG_PATH.read_text().splitlines():
        prefix_matches = [prefix for prefix in (*replacements, *removals) if line.startswith(prefix)]
        if not prefix_matches:
            lines.append(line)
        elif prefix_matches[0] in replacements:
            lines.append(replacements[prefix_matches[0]])
    return "\n".join(lines)


def write_and_load(tmp_path: Path, config_text: str):
    config_path = tmp_path / "config.ini"
    config_path.write_text(config_text)
    return load_config(config_path)


class TestExampleConfig:
    def test_loads_successfully(self):
        config = load_config(EXAMPLE_CONFIG_PATH)

        assert config.cells_per_pack == 16
        assert config.auto_reset_soc_on_float_transition is True
        assert config.battery_ports == (BatteryPortConfig(device="/dev/ttyUSB0", pack_addresses=(1, 2, 3), require_direct_connection=False),)
        assert config.shunt_port == "/dev/ttySH"
        assert config.cell_voltage.float_volts == pytest.approx(3.325)
        assert config.charge_stage.cvl_charger_offset_volts == pytest.approx(0.05)
        assert config.charge_limit.max_amps == pytest.approx(10.0)
        assert config.charge_limit.cell_voltage_curve.fraction_at(3.375) == pytest.approx(1.0)
        assert config.discharge_limit.cell_voltage_curve.fraction_at(2.709) == pytest.approx(0.0)
        assert config.protection.max_temperature_spread_celsius == pytest.approx(10.0)
        assert config.protection.ptc is not None
        assert config.protection.ptc.expected_aux_voltage_by_temperature.value_at(-20.0) == pytest.approx(0.740)


class TestOptionalParts:
    def test_temperature_spread_can_be_omitted(self, tmp_path):
        config = write_and_load(tmp_path, config_text_with(removals=("max_temperature_spread_celsius",)))
        assert config.protection.max_temperature_spread_celsius is None

    def test_ptc_section_can_be_omitted(self, tmp_path):
        text = config_text_with(
            removals=(
                "[ptc_protection]",
                "expected_aux_voltage_by_temperature",
                "max_deviation_percent",
                "temperature_sample_interval_seconds",
                "temperature_filter_process_variance",
                "temperature_sensor_time_constant_minutes",
            )
        )
        config = write_and_load(tmp_path, text)
        assert config.protection.ptc is None


class TestValidation:
    def test_missing_file(self, tmp_path):
        with pytest.raises(ConfigError, match="cannot read config file"):
            load_config(tmp_path / "nonexistent.ini")

    def test_collects_multiple_issues_at_once(self, tmp_path):
        text = config_text_with(
            replacements={
                "cells_per_pack": "cells_per_pack = not_a_number",
                "max_amps = 10.0": "max_ampz = 10.0",
            }
        )
        with pytest.raises(ConfigError) as error:
            write_and_load(tmp_path, text)
        issues = "\n".join(error.value.issues)
        assert "cells_per_pack must be an integer" in issues
        assert "unknown option 'max_ampz'" in issues
        assert "max_amps is missing" in issues

    def test_unknown_section_is_an_error(self, tmp_path):
        with pytest.raises(ConfigError, match=r"unknown section \[typo_section\]"):
            write_and_load(tmp_path, config_text_with() + "\n[typo_section]\nkey = 1\n")

    def test_no_battery_ports_is_an_error(self, tmp_path):
        text = config_text_with(removals=("[battery_port:/dev/ttyUSB0]", "pack_addresses"))
        with pytest.raises(ConfigError, match="at least one"):
            write_and_load(tmp_path, text)

    def test_duplicate_addresses_are_an_error(self, tmp_path):
        text = config_text_with(replacements={"pack_addresses": "pack_addresses = 0x01, 0x01"})
        with pytest.raises(ConfigError, match="duplicate addresses"):
            write_and_load(tmp_path, text)

    def test_cell_voltage_ordering_is_enforced(self, tmp_path):
        text = config_text_with(replacements={"float_volts": "float_volts = 2.5"})
        with pytest.raises(ConfigError, match="min_volts < float_volts <= max_volts"):
            write_and_load(tmp_path, text)

    def test_descending_curve_is_an_error(self, tmp_path):
        text = config_text_with(
            replacements={"cell_voltage_curve = 3.375": "cell_voltage_curve = 3.630:0.00, 3.375:1.00"}
        )
        with pytest.raises(ConfigError, match="ascending"):
            write_and_load(tmp_path, text)

    def test_fraction_above_one_is_an_error(self, tmp_path):
        text = config_text_with(
            replacements={"cell_voltage_curve = 3.375": "cell_voltage_curve = 3.375:1.10, 3.630:0.00"}
        )
        with pytest.raises(ConfigError, match=r"within \[0, 1\]"):
            write_and_load(tmp_path, text)

    def test_ptc_without_shunt_is_an_error(self, tmp_path):
        text = config_text_with(removals=("[shunt]", "port ="))
        with pytest.raises(ConfigError, match=r"\[ptc_protection\] requires a \[shunt\] section"):
            write_and_load(tmp_path, text)
