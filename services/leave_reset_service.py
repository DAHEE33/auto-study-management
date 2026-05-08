from datetime import datetime
from typing import Dict, List

from integrations.google_sheets import sheets_client


class LeaveResetService:
    WEEKLY_RESET_EVENT = "시스템_주휴리셋"
    MONTHLY_RESET_EVENT = "시스템_월휴특휴리셋"

    def run_if_needed(self, now: datetime | None = None):
        current = now or datetime.now()
        members = sheets_client.get_sheet_records("Member_Master")
        admin_rows = sheets_client.get_sheet_records("Admin_Config")

        # 매주 월요일에 주휴를 1로 초기화
        if current.weekday() == 0:
            week_key = current.strftime("%Y-W%W")
            if not self._has_marker(admin_rows, self.WEEKLY_RESET_EVENT, week_key):
                self._reset_weekly_leave(members)
                self._append_marker(week_key, self.WEEKLY_RESET_EVENT)

        # 매월 1일에 월휴(1)를 초기화 (특휴는 무제한이므로 초기화 안 함)
        if current.day == 1:
            month_key = current.strftime("%Y-%m")
            if not self._has_marker(admin_rows, self.MONTHLY_RESET_EVENT, month_key):
                self._reset_monthly_leave_and_special(members)
                self._append_marker(month_key, self.MONTHLY_RESET_EVENT)

    def _has_marker(self, admin_rows: List[Dict], event_type: str, key: str) -> bool:
        for row in admin_rows:
            row_date = str(row.get("날짜", "")).strip()
            row_event = str(row.get("이벤트 타입", "")).strip()
            if row_date == key and row_event == event_type:
                return True
        return False

    def _append_marker(self, key: str, event_type: str):
        marker_row = [key, event_type, "-", "-", "-"]
        sheets_client.append_row("Admin_Config", marker_row)

    def _reset_weekly_leave(self, members: List[Dict]):
        for idx, member in enumerate(members, start=2):
            if str(member.get("상태", "")) != "활동":
                continue
            sheets_client.update_cell("Member_Master", idx, 6, "1")

    def _reset_monthly_leave_and_special(self, members: List[Dict]):
        for idx, member in enumerate(members, start=2):
            if str(member.get("상태", "")) != "활동":
                continue
            sheets_client.update_cell("Member_Master", idx, 7, "1")


leave_reset_service = LeaveResetService()
