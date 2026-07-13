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
FIELD_FULL_DISCHARGE_COUNT = "H5"
FIELD_TOTAL_DRAWN_MILLIAMP_HOURS = "H6"
FIELD_AUTOMATIC_SYNC_COUNT = "H10"
FIELD_DISCHARGED_ENERGY_10WH = "H17"
FIELD_CHARGED_ENERGY_10WH = "H18"
"""Lifetime counters, sent in the device's alternating history frame — a separate frame from
the measurements, which is why they parse into their own value."""


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
    """The device's lifetime counters, converted to display units; drawn Ah as a positive
    magnitude (the wire value is negative per the device convention)."""

    charged_energy_kwh: float
    discharged_energy_kwh: float
    total_ah_drawn_ah: float
    full_discharge_count: int
    automatic_sync_count: int


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
    """Extracts the lifetime counters from a history frame; None when the frame is invalid or
    is not the history frame."""
    if not frame.checksum_valid:
        return None
    charged_kwh = _scaled_int_field(frame, FIELD_CHARGED_ENERGY_10WH, 100.0)
    discharged_kwh = _scaled_int_field(frame, FIELD_DISCHARGED_ENERGY_10WH, 100.0)
    drawn_ah = _scaled_int_field(frame, FIELD_TOTAL_DRAWN_MILLIAMP_HOURS, 1000.0)
    full_discharges = _scaled_int_field(frame, FIELD_FULL_DISCHARGE_COUNT, 1.0)
    syncs = _scaled_int_field(frame, FIELD_AUTOMATIC_SYNC_COUNT, 1.0)
    if None in (charged_kwh, discharged_kwh, drawn_ah, full_discharges, syncs):
        return None
    return HistoryTotals(
        charged_energy_kwh=charged_kwh,
        discharged_energy_kwh=discharged_kwh,
        total_ah_drawn_ah=abs(drawn_ah),
        full_discharge_count=int(full_discharges),
        automatic_sync_count=int(syncs),
    )


def _scaled_int_field(frame: VeDirectFrame, field: str, divisor: float) -> float | None:
    value = frame.fields.get(field)
    if not value:
        return None
    try:
        return int(value) / divisor
    except ValueError:
        return None
