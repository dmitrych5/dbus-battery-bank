"""Assembles core BatterySnapshots from parsed UP16S command responses. Pure — the poller
handles serial I/O, retries, and availability, then calls in here."""

from dataclasses import dataclass

from battery_bank.core.values import BatterySnapshot, BmsLimits, ChainAggregatedLimits, PackIdentity
from battery_bank.transport import up16s

MASTER_ADDRESS = 1
"""A daisy-chain master is always at address 1."""


@dataclass(frozen=True)
class PackInfo:
    """Static identity and display information gathered once at startup."""

    unique_id: str
    hardware_description: str
    production_description: str | None


def build_unique_id(bms_model_and_serial: str | None, pack_serial_number: str, rated_capacity_ah: float) -> str:
    if bms_model_and_serial:
        return bms_model_and_serial
    # Make sure to use the rated capacity, not the full capacity. Rated capacity doesn't get
    # recalculated by the BMS.
    return f"{pack_serial_number}_{rated_capacity_ah}"


def build_production_description(params1: up16s.PackParams1) -> str:
    return (
        f"BMS {params1.bms_year}.{params1.bms_month:02d}.{params1.bms_day:02d}, "
        f"Pack {params1.pack_year}.{params1.pack_month:02d}.{params1.pack_day:02d}"
    )


def build_hardware_description(
    product_information: up16s.ProductInformation | None,
    production_description: str | None,
    pack_status: up16s.PackStatus,
) -> str:
    if product_information is not None:
        info = product_information
        return (
            f"{up16s.from_raw_string(info.project_code)} {up16s.from_raw_string(info.model)}, "
            f"model {info.maybe_model_id}, HW rev {info.maybe_hardware_revision}, "
            f"FW v{info.firmware_major_version}.{info.firmware_minor_version}.{info.firmware_patch_version}"
            + (f", {production_description}" if production_description else "")
        )
    # The master does not pass the full firmware version for slaves. Handle both formats.
    firmware = (
        f"{pack_status.firmware_version >> 8}.{pack_status.firmware_version & 0xFF}"
        if pack_status.firmware_version >= 0x100
        else str(pack_status.firmware_version)
    )
    return f"JBD UP {pack_status.cell_count}S, FW ver {firmware}"


def select_soc_percent(
    pack_status: up16s.PackStatus,
    params2: up16s.PackParams2 | None,
    params2_known_available: bool,
    previous_soc_percent: float | None,
) -> float:
    pack_status_soc = up16s.from_raw_high_resolution_percentage(pack_status.soc)
    if params2 is not None:
        return up16s.from_raw_high_resolution_percentage(params2.high_res_soc)
    if params2_known_available:
        # If the PackParams2 command is available but timed out this time, wait for it to
        # recover. Fall back to the potentially non-high-res SoC from PackStatus only if it
        # differs more than 1% from the last fetched value. This prevents SoC from changing
        # back and forth between high-res and non-high-res values when connection is unstable.
        if previous_soc_percent is not None and abs(previous_soc_percent - pack_status_soc) <= 0.999:
            return previous_soc_percent
    return pack_status_soc


def assemble_snapshot(
    identity: PackIdentity,
    pack_status: up16s.PackStatus,
    params2: up16s.PackParams2 | None,
    individual_status: up16s.IndividualPackStatus | None,
    params2_known_available: bool,
    previous_soc_percent: float | None,
    now_monotonic: float,
) -> BatterySnapshot:
    is_master = identity.address == MASTER_ADDRESS
    # Unlike slaves, the master returns chain-aggregated CCL, DCL, CVL and DVL in PackStatus.
    # Its own non-aggregated CCL/DCL come from IndividualPackStatus (only available on a direct
    # connection); when that is unavailable, the aggregated values stand in — safe, since the
    # bank takes the minimum with the chain limit anyway. Non-aggregated CVL and DVL are not
    # available explicitly, but it appears the master returns them unchanged as the aggregated
    # values.
    chain_limits = (
        ChainAggregatedLimits(
            charge_current_amps=up16s.from_raw_current_to_amps(pack_status.charge_current_limit),
            discharge_current_amps=up16s.from_raw_current_to_amps(pack_status.discharge_current_limit),
        )
        if is_master
        else None
    )
    own_current_limits = individual_status if is_master and individual_status is not None else pack_status

    return BatterySnapshot(
        taken_at_monotonic=now_monotonic,
        identity=identity,
        voltage_volts=up16s.from_raw_pack_voltage_to_volts(pack_status.pack_voltage),
        current_amps=up16s.from_raw_current_with_offset_to_amps(pack_status.current),
        soc_percent=select_soc_percent(pack_status, params2, params2_known_available, previous_soc_percent),
        soh_percent=float(pack_status.soh),
        full_capacity_ah=up16s.from_raw_capacity_to_ah(pack_status.full_capacity),
        rated_capacity_ah=up16s.from_raw_capacity_to_ah(pack_status.rated_capacity),
        remaining_capacity_ah=up16s.from_raw_capacity_to_ah(pack_status.remaining_capacity),
        charge_cycles=pack_status.charge_cycles,
        total_discharge_ah=up16s.from_raw_total_charge_discharge_to_ah(params2.total_discharge) if params2 is not None else None,
        cell_voltages_volts=tuple(up16s.from_raw_cell_voltage_to_volts(raw) for raw in pack_status.cell_voltages),
        cells_balancing=tuple(bool(pack_status.cell_balancing_flags & (1 << cell_index)) for cell_index in range(pack_status.cell_count)),
        cell_temperatures_celsius=tuple(up16s.from_raw_temperature_to_celsius(raw) for raw in pack_status.temperatures),
        mosfet_temperature_celsius=up16s.from_raw_temperature_to_celsius(pack_status.mosfet_temp),
        ambient_temperature_celsius=up16s.from_raw_temperature_to_celsius(pack_status.ambient_temp),
        charge_fet_enabled=bool(pack_status.mosfet_flags & up16s.PackStatus.MOSFET_FLAG_CHARGE_ENABLED),
        discharge_fet_enabled=bool(pack_status.mosfet_flags & up16s.PackStatus.MOSFET_FLAG_DISCHARGE_ENABLED),
        alarms=up16s.decode_alarms(pack_status.fault_flags, pack_status.alarm_flags),
        bms_limits=BmsLimits(
            charge_current_amps=up16s.from_raw_current_to_amps(own_current_limits.charge_current_limit),
            discharge_current_amps=up16s.from_raw_current_to_amps(own_current_limits.discharge_current_limit),
            charge_voltage_volts=up16s.from_raw_dvcc_voltage_to_volts(pack_status.maximum_charge_voltage),
            discharge_voltage_volts=up16s.from_raw_dvcc_voltage_to_volts(pack_status.minimum_discharge_voltage),
        ),
        chain_aggregated_limits=chain_limits,
    )
