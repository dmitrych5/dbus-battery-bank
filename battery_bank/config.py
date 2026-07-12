"""Typed, validated configuration, loaded once at startup.

All configuration access goes through the frozen Config object returned by load_config();
nothing else reads the INI file. Validation collects every problem before raising, so the
operator sees the full list at once, and unknown sections or keys are errors — a typo must
never silently fall back to a default.
"""

from __future__ import annotations

import configparser
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from battery_bank.core.interpolation import InterpolationTable, LimitCurve

BATTERY_PORT_SECTION_PREFIX = "battery_port:"

KNOWN_SECTIONS = (
    "bank",
    "shunt",
    "cell_voltage",
    "charge_stage",
    "cvl_controller",
    "charge_current",
    "discharge_current",
    "limit_update_policy",
    "protection",
    "ptc_protection",
    "staleness",
)

_T = TypeVar("_T")


class ConfigError(Exception):
    """Raised with the complete list of configuration problems."""

    def __init__(self, issues: list[str]):
        self.issues: tuple[str, ...] = tuple(issues)
        super().__init__("invalid configuration:\n" + "\n".join(f"- {issue}" for issue in issues))


@dataclass(frozen=True)
class BatteryPortConfig:
    device: str
    pack_addresses: tuple[int, ...]


@dataclass(frozen=True)
class CellVoltageConfig:
    min_volts: float
    max_volts: float
    float_volts: float


@dataclass(frozen=True)
class ChargeStageConfig:
    absorption_hold_seconds: float
    """How long to hold the maximum charge voltage after the bank is full and balanced."""
    balanced_cell_diff_volts: float
    """Cell voltage difference at or below which cells count as balanced."""
    balanced_cell_diff_restart_margin_volts: float
    """Exceeding balanced_cell_diff_volts by this margin restarts the absorption hold timer."""
    rebulk_soc_percent: float
    """SoC below which the bank leaves Float and returns to Bulk."""
    cvl_charger_offset_volts: float
    """Added to the published CVL so the charger drives the BMS-measured voltage up to the
    charge-stage target. Exists because the absorption->float decision does not yet model the
    balancer's cell-voltage cutoff; retired by roadmap phase 2 in CLAUDE.md."""


@dataclass(frozen=True)
class CvlControllerConfig:
    """Cell-overvoltage I-controller that lowers CVL when the highest cell exceeds its target."""

    volts_per_volt_second: float
    setpoint_margin_volts: float
    """The controller regulates the highest cell towards max cell voltage plus this margin."""


@dataclass(frozen=True)
class CurrentLimitConfig:
    """Limits for one current direction; instantiated once for charge and once for discharge."""

    max_amps: float
    cell_voltage_curve: LimitCurve
    cell_temperature_curve: LimitCurve
    ambient_temperature_curve: LimitCurve
    mosfet_temperature_curve: LimitCurve
    zero_recovery_min_fraction: float
    """After the limit reached zero, hold it there until the recovered limit exceeds this
    fraction of max_amps, preventing rapid flapping around zero."""


@dataclass(frozen=True)
class LimitUpdatePolicyConfig:
    """When recalculated CCL/DCL values are actually published (a drop to zero is always immediate)."""

    min_update_interval_seconds: float
    immediate_update_change_fraction: float


@dataclass(frozen=True)
class PtcProtectionConfig:
    """Overheat detection by comparing the shunt Aux voltage of a PTC thermistor chain against
    the voltage expected at the current (thermal-inertia-corrected) battery temperature."""

    expected_aux_voltage_by_temperature: InterpolationTable
    max_deviation_percent: float
    temperature_sample_interval_seconds: float
    """Must slightly exceed the rate at which pack temperature readings actually change."""
    temperature_filter_process_variance: float
    temperature_sensor_time_constant_minutes: float


@dataclass(frozen=True)
class ProtectionConfig:
    max_temperature_spread_celsius: float | None
    """Latch zero limits when min..max cell temperature across the bank spans more than this.
    None disables the check."""
    ptc: PtcProtectionConfig | None


@dataclass(frozen=True)
class StalenessConfig:
    """Snapshot ages beyond which data counts as stale: alarm plus conservative limits."""

    pack_data_max_age_seconds: float
    shunt_data_max_age_seconds: float


@dataclass(frozen=True)
class Config:
    cells_per_pack: int
    auto_reset_soc_on_float_transition: bool
    battery_ports: tuple[BatteryPortConfig, ...]
    shunt_port: str | None
    cell_voltage: CellVoltageConfig
    charge_stage: ChargeStageConfig
    cvl_controller: CvlControllerConfig
    charge_limit: CurrentLimitConfig
    discharge_limit: CurrentLimitConfig
    limit_update_policy: LimitUpdatePolicyConfig
    protection: ProtectionConfig
    staleness: StalenessConfig


class _IniReader:
    """Typed accessors over ConfigParser that collect issues instead of raising, and track
    which entries were consumed so leftovers can be reported as unknown."""

    def __init__(self, parser: configparser.ConfigParser):
        self._parser = parser
        self._consumed: set[tuple[str, str]] = set()
        self.issues: list[str] = []

    def report(self, issue: str) -> None:
        self.issues.append(issue)

    def has_section(self, section: str) -> bool:
        return self._parser.has_section(section)

    def sections_with_prefix(self, prefix: str) -> list[str]:
        return [name for name in self._parser.sections() if name.startswith(prefix)]

    def get_str(self, section: str, key: str, optional: bool = False) -> str | None:
        if not self._parser.has_option(section, key):
            if not optional:
                self.report(f"[{section}] {key} is missing")
            return None
        self._consumed.add((section, key))
        value = self._parser.get(section, key).strip()
        if not value:
            self.report(f"[{section}] {key} is empty")
            return None
        return value

    def get_float(self, section: str, key: str, optional: bool = False) -> float | None:
        return self._parse(section, key, float, "a number", optional)

    def get_int(self, section: str, key: str, optional: bool = False) -> int | None:
        return self._parse(section, key, int, "an integer", optional)

    def get_bool(self, section: str, key: str) -> bool | None:
        return self._parse(section, key, _parse_bool, "true or false")

    def get_curve(self, section: str, key: str) -> LimitCurve | None:
        return self._parse_points(section, key, LimitCurve)

    def get_table(self, section: str, key: str) -> InterpolationTable | None:
        return self._parse_points(section, key, InterpolationTable)

    def get_addresses(self, section: str, key: str) -> tuple[int, ...] | None:
        raw = self.get_str(section, key)
        if raw is None:
            return None
        try:
            addresses = tuple(int(item.strip(), 0) for item in raw.split(","))
        except ValueError:
            self.report(f"[{section}] {key} must be comma-separated addresses like 0x01, 0x02: got '{raw}'")
            return None
        if len(set(addresses)) != len(addresses):
            self.report(f"[{section}] {key} contains duplicate addresses: {raw}")
            return None
        out_of_range = [address for address in addresses if not 1 <= address <= 255]
        if out_of_range:
            self.report(f"[{section}] {key} addresses must be within 1..255: {out_of_range}")
            return None
        return addresses

    def report_unconsumed_entries(self) -> None:
        for section in self._parser.sections():
            if section not in KNOWN_SECTIONS and not section.startswith(BATTERY_PORT_SECTION_PREFIX):
                self.report(f"unknown section [{section}]")
                continue
            for key in self._parser.options(section):
                if (section, key) not in self._consumed:
                    self.report(f"unknown option '{key}' in section [{section}]")

    def _parse(self, section: str, key: str, convert: Callable[[str], _T], expectation: str, optional: bool = False) -> _T | None:
        raw = self.get_str(section, key, optional)
        if raw is None:
            return None
        try:
            return convert(raw)
        except ValueError:
            self.report(f"[{section}] {key} must be {expectation}: got '{raw}'")
            return None

    def _parse_points(self, section: str, key: str, table_type: type[_T]) -> _T | None:
        raw = self.get_str(section, key)
        if raw is None:
            return None
        inputs: list[float] = []
        outputs: list[float] = []
        try:
            for point in raw.split(","):
                input_text, output_text = point.split(":")
                inputs.append(float(input_text))
                outputs.append(float(output_text))
            return table_type(tuple(inputs), tuple(outputs))
        except ValueError as error:
            self.report(f"[{section}] {key} must be points like '2:0.00, 3:0.20': {error}")
            return None


def _parse_bool(raw: str) -> bool:
    lowered = raw.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    raise ValueError(raw)


def load_config(path: Path) -> Config:
    parser = configparser.ConfigParser(default_section="")
    try:
        with path.open() as config_file:
            parser.read_file(config_file)
    except OSError as error:
        raise ConfigError([f"cannot read config file {path}: {error}"]) from error
    except configparser.Error as error:
        raise ConfigError([f"cannot parse config file {path}: {error}"]) from error

    reader = _IniReader(parser)

    cells_per_pack = reader.get_int("bank", "cells_per_pack")
    auto_reset_soc = reader.get_bool("bank", "auto_reset_soc_on_float_transition")
    battery_ports = _read_battery_ports(reader)
    shunt_port = reader.get_str("shunt", "port") if reader.has_section("shunt") else None

    cell_voltage = CellVoltageConfig(
        min_volts=reader.get_float("cell_voltage", "min_volts"),
        max_volts=reader.get_float("cell_voltage", "max_volts"),
        float_volts=reader.get_float("cell_voltage", "float_volts"),
    )
    charge_stage = ChargeStageConfig(
        absorption_hold_seconds=reader.get_float("charge_stage", "absorption_hold_seconds"),
        balanced_cell_diff_volts=reader.get_float("charge_stage", "balanced_cell_diff_volts"),
        balanced_cell_diff_restart_margin_volts=reader.get_float("charge_stage", "balanced_cell_diff_restart_margin_volts"),
        rebulk_soc_percent=reader.get_float("charge_stage", "rebulk_soc_percent"),
        cvl_charger_offset_volts=reader.get_float("charge_stage", "cvl_charger_offset_volts"),
    )
    cvl_controller = CvlControllerConfig(
        volts_per_volt_second=reader.get_float("cvl_controller", "volts_per_volt_second"),
        setpoint_margin_volts=reader.get_float("cvl_controller", "setpoint_margin_volts"),
    )
    charge_limit = _read_current_limit(reader, "charge_current")
    discharge_limit = _read_current_limit(reader, "discharge_current")
    limit_update_policy = LimitUpdatePolicyConfig(
        min_update_interval_seconds=reader.get_float("limit_update_policy", "min_update_interval_seconds"),
        immediate_update_change_fraction=reader.get_float("limit_update_policy", "immediate_update_change_fraction"),
    )
    protection = ProtectionConfig(
        max_temperature_spread_celsius=reader.get_float("protection", "max_temperature_spread_celsius", optional=True),
        ptc=_read_ptc_protection(reader) if reader.has_section("ptc_protection") else None,
    )
    staleness = StalenessConfig(
        pack_data_max_age_seconds=reader.get_float("staleness", "pack_data_max_age_seconds"),
        shunt_data_max_age_seconds=reader.get_float("staleness", "shunt_data_max_age_seconds"),
    )

    reader.report_unconsumed_entries()
    _validate_cross_field_rules(reader, cells_per_pack, battery_ports, shunt_port, cell_voltage, charge_stage, protection)
    if reader.issues:
        raise ConfigError(reader.issues)

    return Config(
        cells_per_pack=cells_per_pack,
        auto_reset_soc_on_float_transition=auto_reset_soc,
        battery_ports=battery_ports,
        shunt_port=shunt_port,
        cell_voltage=cell_voltage,
        charge_stage=charge_stage,
        cvl_controller=cvl_controller,
        charge_limit=charge_limit,
        discharge_limit=discharge_limit,
        limit_update_policy=limit_update_policy,
        protection=protection,
        staleness=staleness,
    )


def _read_battery_ports(reader: _IniReader) -> tuple[BatteryPortConfig, ...]:
    ports = []
    for section in reader.sections_with_prefix(BATTERY_PORT_SECTION_PREFIX):
        device = section[len(BATTERY_PORT_SECTION_PREFIX):]
        addresses = reader.get_addresses(section, "pack_addresses")
        if not device:
            reader.report(f"section [{section}] is missing the device path after '{BATTERY_PORT_SECTION_PREFIX}'")
        elif addresses:
            ports.append(BatteryPortConfig(device=device, pack_addresses=addresses))
    return tuple(ports)


def _read_current_limit(reader: _IniReader, section: str) -> CurrentLimitConfig:
    return CurrentLimitConfig(
        max_amps=reader.get_float(section, "max_amps"),
        cell_voltage_curve=reader.get_curve(section, "cell_voltage_curve"),
        cell_temperature_curve=reader.get_curve(section, "cell_temperature_curve"),
        ambient_temperature_curve=reader.get_curve(section, "ambient_temperature_curve"),
        mosfet_temperature_curve=reader.get_curve(section, "mosfet_temperature_curve"),
        zero_recovery_min_fraction=reader.get_float(section, "zero_recovery_min_fraction"),
    )


def _read_ptc_protection(reader: _IniReader) -> PtcProtectionConfig:
    return PtcProtectionConfig(
        expected_aux_voltage_by_temperature=reader.get_table("ptc_protection", "expected_aux_voltage_by_temperature"),
        max_deviation_percent=reader.get_float("ptc_protection", "max_deviation_percent"),
        temperature_sample_interval_seconds=reader.get_float("ptc_protection", "temperature_sample_interval_seconds"),
        temperature_filter_process_variance=reader.get_float("ptc_protection", "temperature_filter_process_variance"),
        temperature_sensor_time_constant_minutes=reader.get_float("ptc_protection", "temperature_sensor_time_constant_minutes"),
    )


def _validate_cross_field_rules(
    reader: _IniReader,
    cells_per_pack: int | None,
    battery_ports: tuple[BatteryPortConfig, ...],
    shunt_port: str | None,
    cell_voltage: CellVoltageConfig,
    charge_stage: ChargeStageConfig,
    protection: ProtectionConfig,
) -> None:
    if cells_per_pack is not None and cells_per_pack < 1:
        reader.report(f"[bank] cells_per_pack must be at least 1: got {cells_per_pack}")
    if not battery_ports:
        reader.report(f"at least one [{BATTERY_PORT_SECTION_PREFIX}<device>] section with pack_addresses is required")
    if None not in (cell_voltage.min_volts, cell_voltage.max_volts, cell_voltage.float_volts):
        if not cell_voltage.min_volts < cell_voltage.float_volts <= cell_voltage.max_volts:
            reader.report(
                "[cell_voltage] must satisfy min_volts < float_volts <= max_volts: "
                f"got {cell_voltage.min_volts} / {cell_voltage.float_volts} / {cell_voltage.max_volts}"
            )
    if charge_stage.rebulk_soc_percent is not None and not 0 < charge_stage.rebulk_soc_percent <= 100:
        reader.report(f"[charge_stage] rebulk_soc_percent must be within (0, 100]: got {charge_stage.rebulk_soc_percent}")
    if protection.ptc is not None and shunt_port is None:
        reader.report("[ptc_protection] requires a [shunt] section, since the PTC voltage is read from the shunt Aux input")
