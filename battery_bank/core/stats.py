"""Venus OS ships a trimmed Python without the standard `statistics` module, so the arithmetic
mean lives here."""

from typing import Iterable


def mean(values: Iterable[float]) -> float:
    materialized = list(values)
    return sum(materialized) / len(materialized)
