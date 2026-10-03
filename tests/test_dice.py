import unittest

from rule_engine.dice import DiceExpressionError, parse_dice_expression, roll_dice


class DiceExpressionTests(unittest.TestCase):
    def test_parse_default_count_and_modifier(self):
        self.assertEqual(parse_dice_expression("d20 + 5"), (1, 20, 5, "1d20+5"))

    def test_parse_negative_modifier(self):
        self.assertEqual(parse_dice_expression("2d6-1"), (2, 6, -1, "2d6-1"))

    def test_rejects_malformed_or_injected_expression(self):
        for expression in ("", "1d20; DROP TABLE documents", "1d20/2", "2d6+1 extra"):
            with self.subTest(expression=expression):
                with self.assertRaises(DiceExpressionError):
                    parse_dice_expression(expression)

    def test_rejects_values_outside_resource_bounds(self):
        for expression in ("0d20", "101d20", "1d1", "1d1001", "1d20+100001"):
            with self.subTest(expression=expression):
                with self.assertRaises(DiceExpressionError):
                    parse_dice_expression(expression)


class DiceRollTests(unittest.TestCase):
    @staticmethod
    def sequence(*values):
        values = iter(values)
        return lambda _upper_bound: next(values)

    def test_normal_roll_returns_each_die_and_total_only(self):
        result = roll_dice("2d6+3", randbelow=self.sequence(0, 5))
        self.assertEqual(result, {
            "expression": "2d6+3",
            "mode": "normal",
            "rolls": [1, 6],
            "selected_roll": None,
            "modifier": 3,
            "total": 10,
        })
        self.assertNotIn("success", result)
        self.assertNotIn("damage", result)

    def test_advantage_selects_higher_d20(self):
        result = roll_dice("d20+4", "advantage", randbelow=self.sequence(2, 18))
        self.assertEqual(result["rolls"], [3, 19])
        self.assertEqual(result["selected_roll"], 19)
        self.assertEqual(result["total"], 23)

    def test_disadvantage_selects_lower_d20(self):
        result = roll_dice("1d20-2", "disadvantage", randbelow=self.sequence(19, 3))
        self.assertEqual(result["rolls"], [20, 4])
        self.assertEqual(result["selected_roll"], 4)
        self.assertEqual(result["total"], 2)

    def test_advantage_is_limited_to_one_d20(self):
        with self.assertRaises(DiceExpressionError):
            roll_dice("2d20", "advantage", randbelow=self.sequence(0, 0))

    def test_rejects_unknown_mode(self):
        with self.assertRaises(DiceExpressionError):
            roll_dice("d20", "exploding", randbelow=self.sequence(0))

    def test_rejects_invalid_random_source_result(self):
        with self.assertRaises(RuntimeError):
            roll_dice("d20", randbelow=lambda _n: 20)


if __name__ == "__main__":
    unittest.main()
