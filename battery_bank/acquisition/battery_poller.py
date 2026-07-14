"""Polls the UP16S packs on one serial port and assembles BatterySnapshots.

Retry policy ported from the proven driver: on a failure that's not related to serial
connection interference, give up for this cycle — the next poll cycle retries on its own.
When another process interferes with the port, reopen it (which restores the communication
parameters) and retry after a short delay.
"""

import logging
import time
from typing import Protocol

from battery_bank.acquisition.availability import MAX_AVAILABILITY_RETRIES, AvailabilityStatus, CommandAvailabilityTracker
from battery_bank.acquisition.snapshots import (
    MASTER_ADDRESS,
    PackInfo,
    build_hardware_description,
    build_production_description,
    build_unique_id,
    assemble_snapshot,
)
from battery_bank.config import BatteryPortConfig
from battery_bank.core.values import BatterySnapshot, PackIdentity
from battery_bank.transport import up16s, up16s_raw_window

logger = logging.getLogger(__name__)

SERIAL_TIMEOUT_SECONDS = 1.5
"""It usually takes 0.3-0.6 s to receive a response, so a 1.5 second timeout should be enough."""
RESPONSE_OVERHEAD_LENGTH = up16s.RESPONSE_PAYLOAD_LENGTH_OFFSET + 2 + up16s.CRC_STRUCT.size
"""Length of the header up to and including the payload length field, plus the CRC."""
INTERFERENCE_DELAY_SECONDS = 1.0
"""Delay before retrying when serial interference from another process is detected."""
MAX_INTERFERENCE_RETRY_SECONDS = 60.0
"""How much time to wait for interference to end during discovery."""
MAX_SET_SOC_RETRIES = 3


class Link(Protocol):
    def request(self, request_bytes: bytes, payload_length_offset: int, overhead_length: int) -> bytes | None: ...

    def interference_detected(self) -> bool: ...

    def reopen(self) -> None: ...


class PackPoller:
    """One poller per serial port; pollers on different ports are independent."""

    def __init__(self, port_config: BatteryPortConfig, link: Link, cells_per_pack: int, clock=time.monotonic, sleep=time.sleep):
        self._port_config = port_config
        self._link = link
        self._cells_per_pack = cells_per_pack
        self._clock = clock
        self._sleep = sleep
        self._availability = CommandAvailabilityTracker()
        self._identities: dict[int, PackIdentity] = {}
        self._infos: dict[int, PackInfo] = {}
        self._temperature_counts: dict[int, int] = {}
        """Sensor count seen at discovery; later snapshots must match (see poll())."""
        self._previous_soc: dict[int, float] = {}
        self._pending_soc_resets: dict[int, float] = {}

    def discover(self) -> dict[int, PackInfo]:
        """Identifies packs not yet discovered; returns everything discovered so far. Identity
        requests deliberately retry and block: the BMS serial number is wanted for a stable
        unique identifier — but only after the pack proved to respond to PackStatus at all."""
        for address in self._port_config.pack_addresses:
            if address in self._infos:
                continue
            # If a direct connection is required, try requesting IndividualPackStatus first,
            # which is only available with a direct connection.
            if self._port_config.require_direct_connection:
                if self._send_retrying_on_interference(address, up16s.IndividualPackStatus) is None:
                    continue
            status = self._send_retrying_on_interference(address, up16s.PackStatus)
            if status is None:
                continue
            if status.cell_count != self._cells_per_pack:
                logger.error(
                    "Battery %d reports %d cells but the config says %d per pack; not accepting it until the config matches",
                    address,
                    status.cell_count,
                    self._cells_per_pack,
                )
                continue
            self._temperature_counts[address] = status.temperatures_count

            params1 = self._send_optional(address, up16s.PackParams1, attempts=MAX_AVAILABILITY_RETRIES)
            production = build_production_description(params1) if params1 is not None else None
            # If PackParams1 wasn't successful, it's very unlikely that ProductInformation
            # will be — skip it in that case.
            product_information = (
                self._send_optional(address, up16s.ProductInformation, attempts=MAX_AVAILABILITY_RETRIES) if params1 is not None else None
            )

            unique_id = build_unique_id(
                up16s.from_raw_string(params1.bms_model_and_sn) if params1 is not None else None,
                up16s.from_raw_string(status.pack_serial_number),
                up16s.from_raw_capacity_to_ah(status.rated_capacity),
            )
            self._infos[address] = PackInfo(
                unique_id=unique_id,
                hardware_description=build_hardware_description(product_information, production, status),
                production_description=production,
            )
            self._identities[address] = PackIdentity(unique_id=unique_id, port=self._port_config.device, address=address)
            logger.info("Discovered pack %s at %s address %d", unique_id, self._port_config.device, address)
        return dict(self._infos)

    def poll(self) -> list[BatterySnapshot]:
        """One pass over the discovered packs. A pack that fails to answer simply yields no
        snapshot this cycle; the control core judges staleness by snapshot age."""
        snapshots = []
        for address in self._port_config.pack_addresses:
            identity = self._identities.get(address)
            if identity is None:
                continue
            pending_reset = self._pending_soc_resets.pop(address, None)
            if pending_reset is not None:
                self._set_soc(address, pending_reset)

            status = self._send(address, up16s.PackStatus)
            if status is None:
                # No retry within the cycle, but reopen after interference so that the next
                # cycle communicates with restored port parameters.
                if self._link.interference_detected():
                    logger.warning("Another process is interfering with serial communication")
                    self._link.reopen()
                continue
            if status.cell_count != self._cells_per_pack or status.temperatures_count != self._temperature_counts[address]:
                # Publishing fixes the per-cell and per-sensor D-Bus paths from the first
                # snapshot, so a frame with deviating counts must not flow downstream; treat
                # it like a failed poll and let staleness handle persistence of the condition.
                logger.warning(
                    "Battery %d reported %d cells / %d temperature sensors, expected %d / %d; dropping the snapshot",
                    address,
                    status.cell_count,
                    status.temperatures_count,
                    self._cells_per_pack,
                    self._temperature_counts[address],
                )
                continue
            raw_status = self._validated_raw_status(address, status)
            if raw_status is not None:
                # The raw window covers everything PackParams2 (high-res SoC) and
                # IndividualPackStatus (the master's own limits) would add — except lifetime
                # discharge, which is deliberately left unfetched then.
                params2 = None
                individual_status = None
            else:
                params2 = self._send_optional(address, up16s.PackParams2)
                individual_status = self._send_optional(address, up16s.IndividualPackStatus) if address == MASTER_ADDRESS else None

            snapshot = assemble_snapshot(
                identity,
                status,
                raw_status,
                params2,
                individual_status,
                high_res_soc_known_available=self._high_res_soc_known_available(address),
                previous_soc_percent=self._previous_soc.get(address),
                now_monotonic=self._clock(),
            )
            self._previous_soc[address] = snapshot.soc_percent
            snapshots.append(snapshot)
        return snapshots

    def _validated_raw_status(self, address: int, status: up16s.PackStatus) -> up16s_raw_window.RawStatus | None:
        """The undocumented raw status window must keep proving itself on every read: the
        first response that fails validation against the same cycle's PackStatus (or its own
        internal invariants) disables the command on this battery until restart. See
        transport.up16s_raw_window."""
        raw_status = self._send_optional(address, up16s_raw_window.RawStatus)
        if raw_status is None:
            return None
        failures = up16s_raw_window.validate(raw_status, status, address)
        if failures:
            self._availability.mark_unavailable(address, up16s_raw_window.RawStatus)
            logger.error(
                "Battery %d raw status window failed validation; falling back to the documented commands until restart:\n%s",
                address,
                "\n".join(failures),
            )
            return None
        return raw_status

    def _high_res_soc_known_available(self, address: int) -> bool:
        return any(
            self._availability.status(address, command) is AvailabilityStatus.AVAILABLE
            for command in (up16s_raw_window.RawStatus, up16s.PackParams2)
        )

    def request_soc_reset(self, unique_id: str, soc_percent: float) -> bool:
        """Queues a SoC write for the next poll. SetSoc availability is learned from its own
        write attempts — PackParams2's verdict says nothing about it, because that command can
        be unavailable merely for its partial-read request format (firmware < ~v12) while the
        plain SetSoc write still works."""
        if not 0 <= soc_percent <= 100:
            return False
        for address, identity in self._identities.items():
            if identity.unique_id == unique_id:
                if self._availability.status(address, up16s.SetSoc) is AvailabilityStatus.UNAVAILABLE:
                    return False
                self._pending_soc_resets[address] = soc_percent
                return True
        return False

    def _set_soc(self, address: int, soc_percent: float) -> None:
        payload = up16s.SetSoc.request_payload(soc_percent)
        if self._send_optional(address, up16s.SetSoc, payload, attempts=MAX_SET_SOC_RETRIES) is not None:
            logger.info("Successfully set SOC on battery %d to %.2f%%", address, soc_percent)
        else:
            logger.error("Couldn't set SOC on battery %d", address)

    def _send(self, address: int, command: type[up16s.CommandT], payload: bytes = b"") -> up16s.CommandT | None:
        request = up16s.build_request(address, command, payload)
        response = self._link.request(request, up16s.RESPONSE_PAYLOAD_LENGTH_OFFSET, RESPONSE_OVERHEAD_LENGTH)
        if response is None:
            return None
        try:
            return up16s.parse_response(address, command, response)
        except up16s.FrameError as error:
            logger.warning("Battery %d %s: %s", address, command.__name__, error)
            return None

    def _send_retrying_on_interference(self, address: int, command: type[up16s.CommandT]) -> up16s.CommandT | None:
        """For required commands during discovery: a non-interference failure returns None
        right away (the caller retries discovery on its own), while interference is waited out
        by reopening the port to restore the communication parameters."""
        deadline = self._clock() + MAX_INTERFERENCE_RETRY_SECONDS
        while True:
            result = self._send(address, command)
            if result is not None:
                return result
            if not self._link.interference_detected() or self._clock() >= deadline:
                return None
            logger.warning("Another process is interfering with serial communication. Retrying...")
            self._link.reopen()
            self._sleep(INTERFERENCE_DELAY_SECONDS)

    def _send_optional(
        self, address: int, command: type[up16s.CommandT], payload: bytes = b"", attempts: int = 1
    ) -> up16s.CommandT | None:
        for _ in range(attempts):
            if not self._availability.should_send(address, command):
                return None
            result = self._send(address, command, payload)
            if result is not None:
                if self._availability.record_success(address, command):
                    logger.info("Marking %s command available on battery %d", command.__name__, address)
                return result
            # Failures caused by temporary serial interference from another process say
            # nothing about command availability, so they are not counted.
            if self._reopen_if_interfered():
                continue
            if self._availability.record_failure(address, command):
                message = f"Marking {command.__name__} command unavailable on battery {address}"
                if address == MASTER_ADDRESS:
                    logger.warning(message)
                else:
                    logger.info("%s. This is often normal and depends on your cabling configuration", message)
        return None

    def _reopen_if_interfered(self) -> bool:
        if not self._link.interference_detected():
            return False
        logger.warning("Another process is interfering with serial communication. Retrying...")
        self._link.reopen()
        self._sleep(INTERFERENCE_DELAY_SECONDS)
        return True
