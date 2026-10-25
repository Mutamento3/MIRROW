"""Isolated context tests: no production imports, stores or model calls."""
import ast
import sys
import types
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def ingredient_function(name):
    tree = ast.parse((ROOT / 'context_builder/ingredients.py').read_text(encoding='utf-8'))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    scope = {'datetime': datetime, '_WEEKDAYS_CN': ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']}
    exec(compile(ast.Module(body=[node], type_ignores=[]), '<isolated-ingredient>', 'exec'), scope)
    return scope[name]


class PublicContextDefaults(unittest.TestCase):
    def calendar_result(self, value=None, error=None):
        package = types.ModuleType('calendar_manager')
        package.__path__ = []
        database = types.ModuleType('calendar_manager.database')
        def read_day():
            if error:
                raise error
            return value
        database.is_rest_day_today = read_day
        with patch.dict(sys.modules, {'calendar_manager': package, 'calendar_manager.database': database}):
            return ingredient_function('build_time_section')()

    def test_missing_rest_marker_does_not_invent_work_schedule(self):
        self.assertIn('工作安排未知', self.calendar_result(False))

    def test_rest_marker_preserves_actual_calendar_fact(self):
        self.assertIn('日历将今天标记为休息日', self.calendar_result(True))

    def test_calendar_error_is_unknown_not_weekday_guess(self):
        self.assertIn('日历状态暂不可读', self.calendar_result(error=RuntimeError('synthetic unavailable')))

    def test_no_default_relationship_timeline(self):
        self.assertEqual(ingredient_function('build_timeline_anchor')(), '')


if __name__ == '__main__':
    unittest.main()
