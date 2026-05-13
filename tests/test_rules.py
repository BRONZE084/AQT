import unittest

from aqt.rules import FeeModel, round_lot


class RulesTest(unittest.TestCase):
    def test_round_lot(self) -> None:
        self.assertEqual(round_lot(0), 0)
        self.assertEqual(round_lot(99), 0)
        self.assertEqual(round_lot(100), 100)
        self.assertEqual(round_lot(260), 200)

    def test_fee_model(self) -> None:
        fee_model = FeeModel()
        self.assertAlmostEqual(fee_model.fees("buy", 100, 10), 5.01, places=2)
        self.assertAlmostEqual(fee_model.fees("sell", 100, 10), 5.51, places=2)


if __name__ == "__main__":
    unittest.main()

