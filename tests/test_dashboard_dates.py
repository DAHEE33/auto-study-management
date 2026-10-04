"""Dashboard date regressions without external SDK or Google Sheets access."""
import ast
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import unittest


class DashboardDateTests(unittest.TestCase):
    def render_context(self, today, view="monthly"):
        source = Path(__file__).resolve().parents[1] / "routers" / "dashboard.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        functions = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for node in functions:
            node.decorator_list = []
        sheets = {
            "Member_Master": [{"닉네임": "tester", "상태": "활동"}],
            "Daily_Log": [
                {"닉네임": "tester", "날짜": date, "당일시간": duration, "판정": "PASS"}
                for date, duration in [
                    ("2026-09-28", "9시간 0분"),
                    ("2026-10-01", "1시간 0분"),
                    ("2026-10-02", "2시간 0분"),
                    ("2026-10-05", "3시간 0분"),
                ]
            ],
            "Photo_Auth_History": [],
        }
        namespace = {
            "datetime": SimpleNamespace(now=lambda: today), "timedelta": timedelta,
            "Request": object, "Query": lambda value: value,
            "leave_reset_service": SimpleNamespace(run_if_needed=lambda: None),
            "sheets_client": SimpleNamespace(get_sheet_records=sheets.__getitem__),
            "templates": SimpleNamespace(TemplateResponse=lambda **kwargs: kwargs["context"]),
        }
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), "exec"), namespace)
        return asyncio.run(namespace["view_dashboard"](None, "tester", view))

    def test_october_includes_previous_week_but_totals_only_october(self):
        context = self.render_context(datetime(2026, 10, 5))
        self.assertEqual(context["date_strs"][0], "2026-09-28")
        self.assertEqual(context["date_strs"][-1], "2026-10-05")
        self.assertEqual(context["matrix"]["2026-09-28"]["tester"]["status"], "PASS")
        self.assertEqual(context["my_stats"]["acc_time"], "6시간 0분")
        self.assertEqual(context["leaderboard"][0]["fmt_time"], "6h 0m")

    def test_year_boundary(self):
        context = self.render_context(datetime(2027, 1, 2))
        self.assertEqual(context["date_strs"][0], "2026-12-28")

    def test_month_starting_monday(self):
        context = self.render_context(datetime(2026, 6, 3))
        self.assertEqual(context["date_strs"][0], "2026-06-01")

    def test_weekly_dates_and_totals_are_unchanged(self):
        context = self.render_context(datetime(2026, 10, 1), "weekly")
        self.assertEqual(context["date_strs"][0], "2026-09-28")
        self.assertEqual(context["date_strs"][-1], "2026-10-02")
        self.assertEqual(context["my_stats"]["acc_time"], "12시간 0분")


if __name__ == "__main__":
    unittest.main()
