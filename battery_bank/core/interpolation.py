"""Piecewise-linear interpolation tables, used for limit curves and expected-value lookups."""

from bisect import bisect_right
from dataclasses import dataclass


@dataclass(frozen=True)
class InterpolationTable:
    """Maps an input to an output by linear interpolation, clamped to the outermost points.

    Inputs must be strictly ascending; at least two points are required.
    """

    inputs: tuple[float, ...]
    outputs: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.inputs) != len(self.outputs):
            raise ValueError(f"inputs and outputs differ in length: {len(self.inputs)} vs {len(self.outputs)}")
        if len(self.inputs) < 2:
            raise ValueError("at least two points are required")
        if any(left >= right for left, right in zip(self.inputs, self.inputs[1:])):
            raise ValueError(f"inputs must be strictly ascending: {self.inputs}")

    def value_at(self, input_value: float) -> float:
        if input_value <= self.inputs[0]:
            return self.outputs[0]
        if input_value >= self.inputs[-1]:
            return self.outputs[-1]
        right = bisect_right(self.inputs, input_value)
        left = right - 1
        position_between_points = (input_value - self.inputs[left]) / (self.inputs[right] - self.inputs[left])
        return self.outputs[left] + position_between_points * (self.outputs[right] - self.outputs[left])


@dataclass(frozen=True)
class LimitCurve(InterpolationTable):
    """An InterpolationTable whose outputs are current fractions within [0, 1]."""

    def __post_init__(self) -> None:
        super().__post_init__()
        fractions_out_of_range = [fraction for fraction in self.outputs if not 0.0 <= fraction <= 1.0]
        if fractions_out_of_range:
            raise ValueError(f"fractions must be within [0, 1]: {fractions_out_of_range}")

    def fraction_at(self, input_value: float) -> float:
        return self.value_at(input_value)
