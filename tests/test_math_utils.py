from __future__ import annotations

import math
import unittest

from aqt.math_utils import _rank, _stdev, bollinger_bands, ema, rsi, sma


class StdevTest(unittest.TestCase):
    def test_empty(self) -> None:
        self.assertEqual(_stdev([]), 0.0)
        self.assertEqual(_stdev([1.0]), 0.0)

    def test_known(self) -> None:
        result = _stdev([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0])
        self.assertAlmostEqual(result, 2.138, places=3)


class RankTest(unittest.TestCase):
    def test_empty(self) -> None:
        self.assertEqual(_rank({}, True), {})

    def test_single(self) -> None:
        self.assertEqual(_rank({"A": 5.0}, True), {"A": 1.0})

    def test_higher_better(self) -> None:
        result = _rank({"A": 1.0, "B": 2.0, "C": 3.0}, True)
        self.assertAlmostEqual(result["A"], 0.0)
        self.assertAlmostEqual(result["B"], 0.5)
        self.assertAlmostEqual(result["C"], 1.0)

    def test_lower_better(self) -> None:
        result = _rank({"A": 1.0, "B": 2.0, "C": 3.0}, False)
        self.assertAlmostEqual(result["A"], 1.0)
        self.assertAlmostEqual(result["B"], 0.5)
        self.assertAlmostEqual(result["C"], 0.0)


class SMATest(unittest.TestCase):
    def test_insufficient_data(self) -> None:
        self.assertEqual(sma([1.0, 2.0], 3), [])

    def test_known(self) -> None:
        result = sma([1.0, 2.0, 3.0, 4.0, 5.0], 3)
        self.assertEqual(len(result), 3)
        self.assertAlmostEqual(result[0], 2.0)
        self.assertAlmostEqual(result[1], 3.0)
        self.assertAlmostEqual(result[2], 4.0)


class EMATest(unittest.TestCase):
    def test_insufficient_data(self) -> None:
        self.assertEqual(ema([1.0, 2.0], 3), [])

    def test_known(self) -> None:
        result = ema([1.0, 2.0, 3.0, 4.0, 5.0], 3)
        self.assertEqual(len(result), 3)
        self.assertAlmostEqual(result[0], 2.0)


class RSITest(unittest.TestCase):
    def test_insufficient_data(self) -> None:
        self.assertAlmostEqual(rsi([1.0, 2.0]), 50.0)

    def test_all_up(self) -> None:
        closes = list(range(20))
        self.assertAlmostEqual(rsi(closes, 14), 100.0)

    def test_all_down(self) -> None:
        closes = list(range(20, 0, -1))
        self.assertAlmostEqual(rsi(closes, 14), 0.0)


class BollingerBandsTest(unittest.TestCase):
    def test_insufficient_data(self) -> None:
        self.assertEqual(bollinger_bands([1.0, 2.0], 20), (0.0, 0.0, 0.0))

    def test_known(self) -> None:
        closes = [10.0 + math.sin(i * 0.5) for i in range(25)]
        middle, upper, lower = bollinger_bands(closes, 20, 2.0)
        self.assertGreater(upper, middle)
        self.assertLess(lower, middle)


if __name__ == "__main__":
    unittest.main()
