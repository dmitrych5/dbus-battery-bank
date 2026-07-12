import pytest

from battery_bank.core.interpolation import InterpolationTable, LimitCurve


class TestInterpolationTable:
    def test_interpolates_between_points(self):
        table = InterpolationTable(inputs=(0.0, 10.0), outputs=(100.0, 200.0))
        assert table.value_at(2.5) == pytest.approx(125.0)

    def test_interpolates_within_correct_segment(self):
        table = InterpolationTable(inputs=(0.0, 10.0, 20.0), outputs=(0.0, 100.0, 0.0))
        assert table.value_at(15.0) == pytest.approx(50.0)

    def test_returns_exact_output_at_a_point(self):
        table = InterpolationTable(inputs=(0.0, 10.0, 20.0), outputs=(0.0, 100.0, 0.0))
        assert table.value_at(10.0) == pytest.approx(100.0)

    def test_clamps_below_first_point(self):
        table = InterpolationTable(inputs=(0.0, 10.0), outputs=(100.0, 200.0))
        assert table.value_at(-5.0) == pytest.approx(100.0)

    def test_clamps_above_last_point(self):
        table = InterpolationTable(inputs=(0.0, 10.0), outputs=(100.0, 200.0))
        assert table.value_at(15.0) == pytest.approx(200.0)

    def test_rejects_mismatched_lengths(self):
        with pytest.raises(ValueError, match="differ in length"):
            InterpolationTable(inputs=(0.0, 1.0), outputs=(0.0,))

    def test_rejects_single_point(self):
        with pytest.raises(ValueError, match="two points"):
            InterpolationTable(inputs=(0.0,), outputs=(0.0,))

    def test_rejects_descending_inputs(self):
        with pytest.raises(ValueError, match="ascending"):
            InterpolationTable(inputs=(10.0, 0.0), outputs=(0.0, 1.0))

    def test_rejects_duplicate_inputs(self):
        with pytest.raises(ValueError, match="ascending"):
            InterpolationTable(inputs=(0.0, 0.0, 1.0), outputs=(0.0, 0.5, 1.0))


class TestLimitCurve:
    def test_allows_non_monotonic_fractions(self):
        curve = LimitCurve(inputs=(2.0, 10.0, 42.0, 48.0), outputs=(0.0, 1.0, 1.0, 0.0))
        assert curve.fraction_at(45.0) == pytest.approx(0.5)

    def test_rejects_fraction_above_one(self):
        with pytest.raises(ValueError, match=r"within \[0, 1\]"):
            LimitCurve(inputs=(0.0, 1.0), outputs=(0.0, 1.5))

    def test_rejects_negative_fraction(self):
        with pytest.raises(ValueError, match=r"within \[0, 1\]"):
            LimitCurve(inputs=(0.0, 1.0), outputs=(-0.1, 1.0))
