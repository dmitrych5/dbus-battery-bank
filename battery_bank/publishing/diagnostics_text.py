"""Renders the control core's state into the concise multi-line texts shown on the GUI
parameters page (/Info/ChargeModeDebug and friends) — far more readable on screen than one
D-Bus row per value. Pure functions of the same values the core actually used, so the display
can never drift from the control behavior."""

from typing import Sequence

from battery_bank.config import Config
from battery_bank.core.bank import BankDecision, ControlState
from battery_bank.core.charge_stage import ChargeStage
from battery_bank.core.current_limits import BankCurrentLimit
from battery_bank.core.values import BatterySnapshot, ShuntSnapshot
from battery_bank.publishing.service_values import limitation_text


def pack_diagnostics_values(decision: BankDecision, snapshot: BatterySnapshot, now_monotonic: float) -> dict[str, str]:
    """One pack's debug text: the pieces of the bank decision that are specific to this pack
    (its limit contributions and their sources, what its BMS reports, FET/balancing state,
    data freshness). Bank-level state lives in the aggregate's diagnostics."""
    pack = snapshot
    lines = [
        _pack_limit_line("CCL", decision.charge_limit_detail, pack.identity.unique_id),
        _pack_limit_line("DCL", decision.discharge_limit_detail, pack.identity.unique_id),
        f"BMS limits: charge {pack.bms_limits.charge_current_amps:.0f} A • discharge {pack.bms_limits.discharge_current_amps:.0f} A"
        f" • CVL {pack.bms_limits.charge_voltage_volts:.2f} V",
    ]
    if pack.chain_aggregated_limits is not None:
        lines.append(
            f"chain limits (master): charge {pack.chain_aggregated_limits.charge_current_amps:.0f} A"
            f" • discharge {pack.chain_aggregated_limits.discharge_current_amps:.0f} A"
        )
    lines += [
        f"FETs: charge {_on_off(pack.charge_fet_enabled)} • discharge {_on_off(pack.discharge_fet_enabled)}"
        f" • balancing: {sum(pack.cells_balancing)} cells",
        f"data age: {now_monotonic - pack.taken_at_monotonic:.1f} s",
    ]
    return {"/Info/ChargeModeDebug": "\n".join(lines)}


def _pack_limit_line(label: str, detail: BankCurrentLimit | None, unique_id: str) -> str:
    pack_limit = next((limit for limit in detail.per_pack if limit.pack_unique_id == unique_id), None) if detail is not None else None
    if pack_limit is None:
        return f"{label}: n/a (no fresh bank decision)"
    return f"{label}: {pack_limit.amps:.1f} A ({limitation_text(pack_limit.active_sources, held_at_zero=False)})"


def _on_off(enabled: bool) -> str:
    return "on" if enabled else "OFF"


def diagnostics_values(
    config: Config,
    state: ControlState,
    decision: BankDecision,
    packs: Sequence[BatterySnapshot],
    shunt: ShuntSnapshot | None,
    now_monotonic: float,
) -> dict[str, str]:
    return {
        "/Info/ChargeModeDebug": _overview_text(config, decision, packs, shunt),
        "/Info/ChargeModeDebugFloat": _float_requirements_text(config, state, packs, now_monotonic),
        "/Info/ChargeModeDebugBulk": _rebulk_requirements_text(config, decision, packs),
    }


def _overview_text(config: Config, decision: BankDecision, packs: Sequence[BatterySnapshot], shunt: ShuntSnapshot | None) -> str:
    lines = [
        f"stage: {decision.charge_stage.value} • CVL: {_fmt(decision.cvl_volts, '{:.3f} V')}",
        f"CCL: {decision.ccl_amps:.1f} A • DCL: {decision.dcl_amps:.1f} A",
        f"SoC: {_fmt(decision.soc_percent, '{:.1f}%')} ({decision.soc_source.value})"
        f" • current: {_fmt(decision.current_amps, '{:.2f} A')}",
        _highest_cell_line(packs),
        f"packs fresh: {decision.fresh_pack_count}/{sum(len(port.pack_addresses) for port in config.battery_ports)}"
        f" • shunt fresh: {'yes' if decision.shunt_fresh else 'NO'}",
    ]
    ptc = decision.protections.ptc
    if ptc is not None:
        aux = shunt.aux_voltage_volts if shunt is not None else None
        lines.append(
            f"PTC: aux {_fmt(aux, '{:.3f} V')} • expected {_fmt(ptc.expected_aux_voltage_volts, '{:.3f} V')}"
            f" • deviation {_fmt(ptc.deviation_percent, '{:.1f}%')}"
            f" (max {config.protection.ptc.max_deviation_percent:.0f}%)"
            f" • corrected temp {ptc.corrected_temperature_celsius:.2f} C"
        )
    tripped = decision.protections.state.tripped
    lines.append("trips: " + (", ".join(sorted(kind.value for kind in tripped)) if tripped else "none"))
    return "\n".join(lines)


def _float_requirements_text(config: Config, state: ControlState, packs: Sequence[BatterySnapshot], now_monotonic: float) -> str:
    stage_config = config.charge_stage
    full_voltage = config.cells_per_pack * config.cell_voltage.max_volts - stage_config.full_detection_tolerance_volts
    pack_lines = [
        f"{pack.identity.unique_id}: sum {sum(pack.cell_voltages_volts):.2f}/{full_voltage:.2f} V"
        f" • diff {pack.max_cell_voltage_volts() - pack.min_cell_voltage_volts():.3f}"
        f"/{stage_config.balanced_cell_diff_volts:.3f} V"
        for pack in packs
    ]
    hold_started = state.charge_stage.full_and_balanced_since
    hold_line = (
        f"hold: {now_monotonic - hold_started:.0f}/{stage_config.absorption_hold_seconds:.0f} s"
        if hold_started is not None
        else f"hold: not started (needs every pack full and balanced for {stage_config.absorption_hold_seconds:.0f} s)"
    )
    return "\n".join(["-- switch to float: every pack full and balanced, then hold --", *pack_lines, hold_line])


def _rebulk_requirements_text(config: Config, decision: BankDecision, packs: Sequence[BatterySnapshot]) -> str:
    threshold = config.charge_stage.rebulk_soc_percent
    pack_lines = [f"{pack.identity.unique_id}: SoC {pack.soc_percent:.1f}% {'<' if pack.soc_percent < threshold else '>='} {threshold:.0f}%" for pack in packs]
    applicability = "" if decision.charge_stage in (ChargeStage.FLOAT, ChargeStage.FLOAT_TRANSITION) else " (only applies in float)"
    return "\n".join([f"-- switch back to bulk: every pack SoC below {threshold:.0f}%{applicability} --", *pack_lines])


def _highest_cell_line(packs: Sequence[BatterySnapshot]) -> str:
    if not packs:
        return "highest cell: no pack data"
    highest = max(packs, key=lambda pack: pack.max_cell_voltage_volts())
    cell_number = highest.cell_voltages_volts.index(highest.max_cell_voltage_volts()) + 1
    bank_diff = max(pack.max_cell_voltage_volts() for pack in packs) - min(pack.min_cell_voltage_volts() for pack in packs)
    return f"highest cell: {highest.max_cell_voltage_volts():.3f} V ({highest.identity.unique_id} C{cell_number}) • bank cell diff: {bank_diff:.3f} V"


def _fmt(value: float | None, format_string: str) -> str:
    return format_string.format(value) if value is not None else "n/a"
