"""Tracks which UP16S commands are answered by which pack.

Not all commands are available on certain ports and battery addresses: it depends on the
specific combination of BMS firmware, cabling, and the port used. Learning availability avoids
sending optional requests that are ignored on a particular combination, so we don't add
unnecessary delays by waiting for responses that never come. Each serial port needs its own
tracker instance.
"""

from dataclasses import dataclass, field
from enum import Enum

MAX_AVAILABILITY_RETRIES = 5
"""Failed attempts before declaring a command on a battery address unavailable."""


class AvailabilityStatus(Enum):
    UNKNOWN = "unknown"  # will retry until MAX_AVAILABILITY_RETRIES
    AVAILABLE = "available"  # answered at least once; no further availability determination
    UNAVAILABLE = "unavailable"  # never answered after all retries; not sent anymore


@dataclass
class _CommandRecord:
    status: AvailabilityStatus
    failed_attempts: int = 0


@dataclass
class CommandAvailabilityTracker:
    _records: dict[tuple[int, str], _CommandRecord] = field(default_factory=dict)

    def status(self, address: int, command: type) -> AvailabilityStatus:
        return self._record(address, command).status

    def should_send(self, address: int, command: type) -> bool:
        return self._record(address, command).status is not AvailabilityStatus.UNAVAILABLE

    def record_success(self, address: int, command: type) -> bool:
        """Returns True when this success newly established availability (worth logging)."""
        record = self._record(address, command)
        newly_available = record.status is not AvailabilityStatus.AVAILABLE
        record.status = AvailabilityStatus.AVAILABLE
        return newly_available

    def record_failure(self, address: int, command: type) -> bool:
        """Counts a failed attempt that was not caused by temporary serial interference from
        another process (the caller filters those out). Returns True when the command just
        became unavailable (worth logging; often normal, depending on cabling configuration)."""
        record = self._record(address, command)
        if record.status is not AvailabilityStatus.UNKNOWN:
            return False
        record.failed_attempts += 1
        if record.failed_attempts >= MAX_AVAILABILITY_RETRIES:
            record.status = AvailabilityStatus.UNAVAILABLE
            return True
        return False

    def _record(self, address: int, command: type) -> _CommandRecord:
        key = (address, command.__name__)
        if key not in self._records:
            # PackStatus is treated as always available by the caller sending it
            # unconditionally; every record here starts unknown.
            self._records[key] = _CommandRecord(AvailabilityStatus.UNKNOWN)
        return self._records[key]
