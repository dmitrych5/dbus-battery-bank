"""Maps the bank decision and pack snapshots to D-Bus path values — pure dictionaries; the
D-Bus service objects push them onto the bus. The aggregate service is the DVCC battery
monitor; per-pack services are read-only projections for VRM logging and debugging.

All VRM-schema accommodations are confined to vrm_metric_workarounds(): VRM has no fields for
PTC diagnostics, so they ride on unused standard paths with honest names on this side.
"""

from typing import Sequence

from battery_bank.config import Config
from battery_bank.core.bank import BankDecision
from battery_bank.core.current_limits import LimitSource, PackCurrentLimit
from battery_bank.core.stats import mean
from battery_bank.core.values import AlarmSeverity, BatterySnapshot, PackAlarms, ShuntSnapshot

ALARM_PATHS = {
    "high_voltage": "/Alarms/HighVoltage",
    "low_voltage": "/Alarms/LowVoltage",
    "high_cell_voltage": "/Alarms/HighCellVoltage",
    "low_cell_voltage": "/Alarms/LowCellVoltage",
    "low_soc": "/Alarms/LowSoc",
    "high_charge_current": "/Alarms/HighChargeCurrent",
    "high_discharge_current": "/Alarms/HighDischargeCurrent",
    "cell_imbalance": "/Alarms/CellImbalance",
    "internal_failure": "/Alarms/InternalFailure",
    "high_charge_temperature": "/Alarms/HighChargeTemperature",
    "low_charge_temperature": "/Alarms/LowChargeTemperature",
    "high_temperature": "/Alarms/HighTemperature",
    "low_temperature": "/Alarms/LowTemperature",
    "high_internal_temperature": "/Alarms/HighInternalTemperature",
}


def aggregate_service_values(
    config: Config,
    decision: BankDecision,
    packs: Sequence[BatterySnapshot],
    shunt: ShuntSnapshot | None,
    service_internal_alarm: bool = False,
) -> dict[str, object]:
    """service_internal_alarm raises /Alarms/InternalFailure for faults of this service itself
    (corrupt state file, repeated cycle failures) — the log alone is not operator-visible."""
    expected_pack_count = sum(len(port.pack_addresses) for port in config.battery_ports)
    remaining_ah = sum(pack.remaining_capacity_ah for pack in packs)
    values: dict[str, object] = {
        "/Dc/0/Voltage": _rounded(decision.voltage_volts, 2),
        "/Dc/0/Current": _rounded(decision.current_amps, 2),
        "/Dc/0/Power": _rounded(decision.power_watts, 2),
        "/Soc": _rounded(decision.soc_percent, 2),
        "/Capacity": _rounded(remaining_ah, 2) if packs else None,
        "/InstalledCapacity": _rounded(sum(pack.full_capacity_ah for pack in packs), 2) if packs else None,
        "/ConsumedAmphours": _rounded(decision.consumed_ah, 2),
        "/TimeToGo": _time_to_go_seconds(remaining_ah, decision.current_amps) if packs else None,
        "/Dc/0/Temperature": _bank_temperature(packs),
        # The hottest ambient sensor matters; shown by stock GUI-v2 as "Air temperature".
        "/AirTemperature": max(pack.ambient_temperature_celsius for pack in packs) if packs else None,
        "/System/NrOfCellsPerBattery": config.cells_per_pack,
        "/System/NrOfModulesOnline": decision.fresh_pack_count,
        "/System/NrOfModulesOffline": expected_pack_count - decision.fresh_pack_count,
        "/System/NrOfModulesBlockingCharge": 0 if decision.allow_to_charge else expected_pack_count,
        "/System/NrOfModulesBlockingDischarge": 0 if decision.allow_to_discharge else expected_pack_count,
        "/Io/AllowToCharge": int(decision.allow_to_charge),
        "/Io/AllowToDischarge": int(decision.allow_to_discharge),
        "/Balancing": int(any(any(pack.cells_balancing) for pack in packs)),
        "/Info/MaxChargeVoltage": (
            round(decision.cvl_volts + config.charge_stage.cvl_charger_offset_volts, 2) if decision.cvl_volts is not None else None
        ),
        "/Info/MaxChargeCurrent": round(decision.ccl_amps, 1),
        "/Info/MaxDischargeCurrent": round(decision.dcl_amps, 1),
        "/Info/ChargeMode": decision.charge_stage.value,
        "/Info/ChargeLimitation": _bank_limitation_text(decision, decision.charge_limit_detail),
        "/Info/DischargeLimitation": _bank_limitation_text(decision, decision.discharge_limit_detail),
    }
    values.update(_cell_extremes(packs))
    values.update(_alarm_values(decision.alarms))
    values["/Alarms/BmsCable"] = int(decision.cable_alarm)
    if service_internal_alarm:
        values["/Alarms/InternalFailure"] = int(AlarmSeverity.ALARM)
    values.update(vrm_metric_workarounds(decision, packs, shunt))
    return values


def pack_service_values(decision: BankDecision, snapshot: BatterySnapshot) -> dict[str, object]:
    pack = snapshot
    values: dict[str, object] = {
        "/Dc/0/Voltage": round(pack.voltage_volts, 2),
        "/Dc/0/Current": round(pack.current_amps, 2),
        "/Dc/0/Power": round(pack.voltage_volts * pack.current_amps, 2),
        "/Soc": round(pack.soc_percent, 2),
        "/Soh": round(pack.soh_percent, 2),
        "/InstalledCapacity": round(pack.full_capacity_ah, 2),
        "/Capacity": round(pack.remaining_capacity_ah, 2),
        "/ConsumedAmphours": round(-(pack.full_capacity_ah - pack.remaining_capacity_ah), 2),
        "/Dc/0/Temperature": round(mean(pack.cell_temperatures_celsius), 1),
        "/AirTemperature": pack.ambient_temperature_celsius,
        "/System/MOSTemperature": pack.mosfet_temperature_celsius,
        "/System/NrOfCellsPerBattery": len(pack.cell_voltages_volts),
        "/System/MinCellVoltage": pack.min_cell_voltage_volts(),
        "/System/MaxCellVoltage": pack.max_cell_voltage_volts(),
        "/System/MinVoltageCellId": f"C{pack.cell_voltages_volts.index(pack.min_cell_voltage_volts()) + 1}",
        "/System/MaxVoltageCellId": f"C{pack.cell_voltages_volts.index(pack.max_cell_voltage_volts()) + 1}",
        "/System/MinCellTemperature": min(pack.cell_temperatures_celsius),
        "/System/MaxCellTemperature": max(pack.cell_temperatures_celsius),
        "/Io/AllowToCharge": int(pack.charge_fet_enabled),
        "/Io/AllowToDischarge": int(pack.discharge_fet_enabled),
        "/Balancing": int(any(pack.cells_balancing)),
        "/Voltages/Sum": round(sum(pack.cell_voltages_volts), 2),
        "/Voltages/Diff": round(pack.max_cell_voltage_volts() - pack.min_cell_voltage_volts(), 3),
        "/History/ChargeCycles": pack.charge_cycles,
        # Negative by the Victron convention.
        "/History/TotalAhDrawn": -pack.total_discharge_ah if pack.total_discharge_ah is not None else None,
        "/Info/ChargeMode": decision.charge_stage.value,
    }
    for index, (voltage, balancing) in enumerate(zip(pack.cell_voltages_volts, pack.cells_balancing), start=1):
        values[f"/Voltages/Cell{index}"] = voltage
        values[f"/Balances/Cell{index}"] = int(balancing)
    for index, temperature in enumerate(pack.cell_temperatures_celsius, start=1):
        values[f"/System/Temperature{index}"] = temperature
        values[f"/System/Temperature{index}Name"] = f"Temp {index}"
    values.update(_alarm_values(pack.alarms))
    values.update(_pack_limit_values(decision.charge_limit_detail, pack.identity.unique_id, "/Info/MaxChargeCurrent", "/Info/ChargeLimitation"))
    values.update(_pack_limit_values(decision.discharge_limit_detail, pack.identity.unique_id, "/Info/MaxDischargeCurrent", "/Info/DischargeLimitation"))
    return values


def vrm_metric_workarounds(decision: BankDecision, packs: Sequence[BatterySnapshot], shunt: ShuntSnapshot | None) -> dict[str, object]:
    """PTC diagnostics on repurposed standard paths, so VRM graphs them for drift monitoring:
    - PTC chain voltage (times 10 for resolution) as the starter battery voltage
    - PTC deviation percent as the mid-voltage deviation
    - measured and inertia-corrected bank temperatures as the min/max starter voltage history
    """
    ptc = decision.protections.ptc
    aux_voltage = shunt.aux_voltage_volts if shunt is not None else None
    return {
        "/Dc/1/Voltage": round(aux_voltage * 10, 2) if aux_voltage is not None else None,
        "/Dc/0/MidVoltageDeviation": round(ptc.deviation_percent, 2) if ptc is not None and ptc.deviation_percent is not None else None,
        "/History/MinimumStarterVoltage": _bank_temperature(packs),
        "/History/MaximumStarterVoltage": round(ptc.corrected_temperature_celsius, 2) if ptc is not None else None,
    }


def limitation_text(active_sources: tuple[LimitSource, ...], held_at_zero: bool) -> str:
    return ", ".join(source.value for source in active_sources) + (" *" if held_at_zero else "")


def _bank_limitation_text(decision: BankDecision, detail) -> str:
    if decision.protections.zero_limits_required:
        return "Protection tripped: " + ", ".join(kind.value for kind in sorted(decision.protections.state.tripped, key=lambda kind: kind.name))
    if not decision.all_packs_fresh:
        return "Stale battery data"
    if not decision.shunt_fresh:
        return "Stale shunt data"
    if detail is None:
        return ""
    return limitation_text(detail.active_sources, detail.held_at_zero)


def _pack_limit_values(detail, unique_id: str, current_path: str, limitation_path: str) -> dict[str, object]:
    pack_limit: PackCurrentLimit | None = None
    if detail is not None:
        pack_limit = next((limit for limit in detail.per_pack if limit.pack_unique_id == unique_id), None)
    if pack_limit is None:
        return {current_path: None, limitation_path: None}
    return {
        current_path: round(pack_limit.amps, 1),
        limitation_path: limitation_text(pack_limit.active_sources, held_at_zero=False),
    }


def _alarm_values(alarms: PackAlarms) -> dict[str, int]:
    return {path: int(getattr(alarms, category)) for category, path in ALARM_PATHS.items()}


def _cell_extremes(packs: Sequence[BatterySnapshot]) -> dict[str, object]:
    if not packs:
        return {}
    lowest = min(packs, key=lambda pack: pack.min_cell_voltage_volts())
    highest = max(packs, key=lambda pack: pack.max_cell_voltage_volts())
    coldest = min(packs, key=lambda pack: min(pack.cell_temperatures_celsius))
    hottest = max(packs, key=lambda pack: max(pack.cell_temperatures_celsius))
    return {
        "/System/MinCellVoltage": lowest.min_cell_voltage_volts(),
        "/System/MinVoltageCellId": f"{lowest.identity.unique_id} C{lowest.cell_voltages_volts.index(lowest.min_cell_voltage_volts()) + 1}",
        "/System/MaxCellVoltage": highest.max_cell_voltage_volts(),
        "/System/MaxVoltageCellId": f"{highest.identity.unique_id} C{highest.cell_voltages_volts.index(highest.max_cell_voltage_volts()) + 1}",
        "/System/MinCellTemperature": min(coldest.cell_temperatures_celsius),
        "/System/MinTemperatureCellId": f"{coldest.identity.unique_id} T{coldest.cell_temperatures_celsius.index(min(coldest.cell_temperatures_celsius)) + 1}",
        "/System/MaxCellTemperature": max(hottest.cell_temperatures_celsius),
        "/System/MaxTemperatureCellId": f"{hottest.identity.unique_id} T{hottest.cell_temperatures_celsius.index(max(hottest.cell_temperatures_celsius)) + 1}",
        "/Voltages/Sum": round(mean(sum(pack.cell_voltages_volts) for pack in packs), 2),
        "/Voltages/Diff": round(
            max(pack.max_cell_voltage_volts() for pack in packs) - min(pack.min_cell_voltage_volts() for pack in packs), 3
        ),
    }


def _bank_temperature(packs: Sequence[BatterySnapshot]) -> float | None:
    return round(mean(mean(pack.cell_temperatures_celsius) for pack in packs), 2) if packs else None


def _time_to_go_seconds(remaining_ah: float, current_amps: float | None) -> int | None:
    if current_amps is None or current_amps >= 0:
        return None
    return int(remaining_ah / -current_amps * 3600)


def _rounded(value: float | None, digits: int) -> float | None:
    return round(value, digits) if value is not None else None
