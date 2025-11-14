from django.test import SimpleTestCase

from core.fields import ConditionEvaluationError, _evaluate_sql_condition


class ConditionEvaluationTests(SimpleTestCase):
    def test_equality_checks(self):
        self.assertTrue(_evaluate_sql_condition("1 = 1"))
        self.assertFalse(_evaluate_sql_condition("1 = 0"))

    def test_in_operators(self):
        self.assertTrue(_evaluate_sql_condition("1 IN (1, 2)"))
        self.assertTrue(_evaluate_sql_condition("1 NOT IN (2, 3)"))
        self.assertFalse(_evaluate_sql_condition("1 NOT IN (1, 3)"))

    def test_null_comparisons(self):
        self.assertTrue(_evaluate_sql_condition("NULL IS NULL"))
        self.assertFalse(_evaluate_sql_condition("NULL IS NOT NULL"))

    def test_not_operator(self):
        self.assertTrue(_evaluate_sql_condition("NOT (1 = 0)"))
        self.assertFalse(_evaluate_sql_condition("NOT 1 = 1"))

    def test_unknown_identifier_raises_error(self):
        with self.assertRaises(ConditionEvaluationError):
            _evaluate_sql_condition("value = 1")

    def test_function_call_rejected(self):
        with self.assertRaises(ConditionEvaluationError):
            _evaluate_sql_condition("ABS(-1) > 0")
