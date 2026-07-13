"""VE.Direct text protocol parser: raw bytes in, checksum-validated frames out. I/O-free; the
serial port handling lives in the acquisition layer.

Frame boundary accounting follows the device stream: each frame's leading CR LF is attributed
to the previous frame, which balances out in steady state (every accumulated frame contains
exactly one full CR LF pair per field plus one trailing pair). Consequences, both harmless
since the device repeats frames every second: the first frame after connecting fails its
checksum, and a checksum byte that happens to equal a line delimiter drops one or two frames.
"""

from dataclasses import dataclass

MAX_BUFFER_BYTES = 8192
TRIMMED_BUFFER_BYTES = 4096

CHECKSUM_FIELD = "Checksum"

FIELD_CURRENT_MILLIAMPS = "I"
FIELD_SOC_PERMILLE = "SOC"
FIELD_CONSUMED_MILLIAMP_HOURS = "CE"
FIELD_AUX_VOLTAGE_MILLIVOLTS = "VS"
"""The shunt reports its Aux input here when the input is configured as starter battery."""

_HISTORY_TOTALS_FIELDS = (
    # (HistoryTotals attribute, VE.Direct field, divisor to display units)
    ("deepest_discharge_ah", "H1", 1000.0),
    ("last_discharge_ah", "H2", 1000.0),
    ("average_discharge_ah", "H3", 1000.0),
    ("charge_cycles", "H4", 1.0),
    ("full_discharge_count", "H5", 1.0),
    ("total_ah_drawn_ah", "H6", 1000.0),
    ("minimum_voltage_volts", "H7", 1000.0),
    ("maximum_voltage_volts", "H8", 1000.0),
    ("automatic_sync_count", "H10", 1.0),
    ("discharged_energy_kwh", "H17", 100.0),
    ("charged_energy_kwh", "H18", 100.0),
)
"""The device's lifetime history, sent in the alternating history frame — a separate frame
from the measurements, which is why it parses into its own value."""


@dataclass(frozen=True)
class VeDirectFrame:
    fields: dict[str, str]
    checksum_valid: bool


@dataclass(frozen=True)
class ShuntReading:
    current_amps: float
    soc_percent: float
    consumed_ah: float
    """Negative when energy was consumed, following the device convention."""
    aux_voltage_volts: float | None


@dataclass(frozen=True)
class HistoryTotals:
    """The device's lifetime history in display units, keeping the device sign conventions
    (discharge Ah quantities are negative)."""

    deepest_discharge_ah: float
    last_discharge_ah: float
    average_discharge_ah: float
    charge_cycles: int
    full_discharge_count: int
    total_ah_drawn_ah: float
    minimum_voltage_volts: float
    maximum_voltage_volts: float
    automatic_sync_count: int
    discharged_energy_kwh: float
    charged_energy_kwh: float


class VeDirectParser:
    """Incremental and stateful, but I/O-free: feed raw bytes, receive complete frames."""

    def __init__(self):
        self._buffer = b""
        self._frame_fields: dict[str, str] = {}
        self._checksum = 0

    def feed(self, data: bytes) -> list[VeDirectFrame]:
        self._buffer += data
        if len(self._buffer) > MAX_BUFFER_BYTES:
            self._buffer = self._buffer[-TRIMMED_BUFFER_BYTES:]
            self._frame_fields = {}
            self._checksum = 0
        frames = []
        while (newline_index := self._buffer.find(b"\n")) != -1:
            line = self._buffer[:newline_index]
            self._checksum = (self._checksum + sum(self._buffer[: newline_index + 1])) & 0xFF
            self._buffer = self._buffer[newline_index + 1 :]
            frame = self._consume_line(line)
            if frame is not None:
                frames.append(frame)
        return frames

    def _consume_line(self, line: bytes) -> VeDirectFrame | None:
        text = line.decode("ascii", errors="replace")
        if "\t" not in text:
            return None
        key, value = (part.strip() for part in text.split("\t", 1))
        self._frame_fields[key] = value
        if key != CHECKSUM_FIELD:
            return None
        frame = VeDirectFrame(fields=self._frame_fields, checksum_valid=self._checksum == 0)
        self._frame_fields = {}
        self._checksum = 0
        return frame


def parse_shunt_reading(frame: VeDirectFrame) -> ShuntReading | None:
    """Extracts the values the bank needs; None when the frame is invalid or incomplete."""
    if not frame.checksum_valid:
        return None
    current_amps = _scaled_int_field(frame, FIELD_CURRENT_MILLIAMPS, 1000.0)
    soc_percent = _scaled_int_field(frame, FIELD_SOC_PERMILLE, 10.0)
    consumed_ah = _scaled_int_field(frame, FIELD_CONSUMED_MILLIAMP_HOURS, 1000.0)
    if current_amps is None or soc_percent is None or consumed_ah is None:
        return None
    return ShuntReading(
        current_amps=current_amps,
        soc_percent=soc_percent,
        consumed_ah=consumed_ah,
        aux_voltage_volts=_scaled_int_field(frame, FIELD_AUX_VOLTAGE_MILLIVOLTS, 1000.0),
    )


def parse_history_totals(frame: VeDirectFrame) -> HistoryTotals | None:
    """Extracts the lifetime history from a history frame; None when the frame is invalid or
    is not the history frame."""
    if not frame.checksum_valid:
        return None
    values = {name: _scaled_int_field(frame, field, divisor) for name, field, divisor in _HISTORY_TOTALS_FIELDS}
    if None in values.values():
        return None
    counts = {name for name, _, divisor in _HISTORY_TOTALS_FIELDS if divisor == 1.0}
    return HistoryTotals(**{name: int(value) if name in counts else value for name, value in values.items()})


def _scaled_int_field(frame: VeDirectFrame, field: str, divisor: float) -> float | None:
    value = frame.fields.get(field)
    if not value:
        return None
    try:
        return int(value) / divisor
    except ValueError:
        return None
