import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List

# 프로젝트 루트 경로 추가 (모듈 import 위함)
sys.path.append(str(Path(__file__).parent.parent))

import gspread
from integrations.google_sheets import sheets_client


LEAVE_TYPES = {"주휴", "월휴", "특휴", "반휴"}


def _to_col_letter(col_num: int) -> str:
    """1-based column index -> Excel column letter."""
    result = ""
    n = col_num
    while n > 0:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result


def _parse_penalty(raw: str) -> int:
    try:
        return int(str(raw).replace(",", "").strip())
    except ValueError:
        return 0


def _build_half_leave_order_map(daily_logs: List[Dict]) -> Dict[tuple, int]:
    """
    (날짜, 닉네임) 기준 반휴 사용 순서를 계산합니다.
    같은 주(ISO week) 안에서 닉네임별 반휴 1회차/2회차를 부여합니다.
    """
    entries = []
    for log in daily_logs:
        leave_type = str(log.get("유형", "")).strip()
        if leave_type != "반휴":
            continue
        date_str = str(log.get("날짜", "")).strip()
        nick = str(log.get("닉네임", "")).strip()
        if not date_str or not nick:
            continue
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        entries.append((d, nick, date_str))

    entries.sort(key=lambda x: (x[0], x[1]))

    weekly_counter: Dict[tuple, int] = {}
    order_map: Dict[tuple, int] = {}
    for d, nick, date_str in entries:
        iso_year, iso_week, _ = d.isocalendar()
        weekly_key = (iso_year, iso_week, nick)
        weekly_counter[weekly_key] = weekly_counter.get(weekly_key, 0) + 1
        order_map[(date_str, nick)] = weekly_counter[weekly_key]

    return order_map


def _build_cell_value(log: Dict, half_leave_order: int | None = None) -> str:
    """
    Daily_Summary 한 셀에 들어갈 값을 구성합니다.
    우선순위: 휴무 > 벌금 > 출석 > 빈값
    """
    leave_type = str(log.get("유형", "")).strip()
    penalty = _parse_penalty(str(log.get("벌금액", "0")))
    status = str(log.get("판정", "")).strip()

    if leave_type in LEAVE_TYPES:
        if leave_type == "반휴":
            if half_leave_order in (1, 2):
                return f"반휴{half_leave_order}"
            return "반휴"
        return leave_type
    if penalty < 0:
        return str(penalty)
    if status and status != "-":
        return "o"
    return "-"


def run_daily_summary_job():
    """
    [매일 정오(12:05) 실행용]
    Daily_Log 기준 최근 3일(D-1~D-3) 요약을 Daily_Summary에 반영합니다.
    """
    started_at = datetime.now()
    print("🚀 [Batch] Starting Daily Summary Job...")
    print(f"[Batch] Started At: {started_at.strftime('%Y-%m-%d %H:%M:%S')}")

    if sheets_client.is_mock:
        print("[MOCK] Daily Summary Job skipped in mock mode.")
        return

    # 1) 요약 대상 날짜: 어제부터 3일
    target_dates: List[str] = []
    for delta in range(1, 4):
        d = (started_at - timedelta(days=delta)).strftime("%Y-%m-%d")
        target_dates.append(d)
    print(f"[Batch] Target Dates: {target_dates}")

    # 2) 활동 멤버 닉네임(헤더)
    members = sheets_client.get_sheet_records("Member_Master")
    nicknames = [
        str(m.get("닉네임", "")).strip()
        for m in members
        if str(m.get("상태", "")).strip() == "활동" and str(m.get("닉네임", "")).strip()
    ]
    nicknames = sorted(set(nicknames))
    print(f"[Batch] Active Nicknames: {len(nicknames)}")

    # 3) Daily_Log에서 대상 날짜 데이터 추출
    daily_logs = sheets_client.get_sheet_records("Daily_Log")
    half_leave_order_map = _build_half_leave_order_map(daily_logs)
    log_map: Dict[tuple, Dict] = {}
    for log in daily_logs:
        d = str(log.get("날짜", "")).strip()
        n = str(log.get("닉네임", "")).strip()
        if d in target_dates and n:
            log_map[(d, n)] = log

    # 4) Daily_Summary 시트 확보
    try:
        ws_summary = sheets_client.spreadsheet.worksheet("Daily_Summary")
    except gspread.exceptions.WorksheetNotFound:
        ws_summary = sheets_client.spreadsheet.add_worksheet(title="Daily_Summary", rows="300", cols="200")
        print("✔️ 'Daily_Summary' 시트 생성 완료")

    # 5) 헤더 보정 (날짜 + 활동 닉네임)
    target_headers = ["날짜"] + nicknames
    current_headers = ws_summary.row_values(1)
    if current_headers != target_headers:
        end_col = _to_col_letter(len(target_headers))
        ws_summary.update(f"A1:{end_col}1", [target_headers])
        print("✔️ Daily_Summary 헤더 갱신 완료")

    # 6) 기존 날짜->행 인덱스 맵
    date_col = ws_summary.col_values(1)  # A열
    date_row_map: Dict[str, int] = {}
    for row_idx, value in enumerate(date_col, start=1):
        if row_idx == 1:
            continue
        date_key = str(value).strip()
        if date_key:
            date_row_map[date_key] = row_idx

    # 7) 대상 날짜 upsert (행 단위)
    updated_rows = 0
    for d in target_dates:
        row_values = [d]
        for nick in nicknames:
            log = log_map.get((d, nick))
            half_leave_order = half_leave_order_map.get((d, nick))
            row_values.append(_build_cell_value(log, half_leave_order) if log else "-")

        target_row_idx = date_row_map.get(d)
        if not target_row_idx:
            ws_summary.append_row(row_values)
            updated_rows += 1
            print(f"➕ Daily_Summary 행 추가: {d}")
            continue

        end_col = _to_col_letter(len(row_values))
        ws_summary.update(f"A{target_row_idx}:{end_col}{target_row_idx}", [row_values])
        updated_rows += 1
        print(f"🔄 Daily_Summary 행 갱신: {d} (row={target_row_idx})")

    sheets_client.clear_cache("Daily_Summary")

    ended_at = datetime.now()
    print(f"[Batch] Updated Rows: {updated_rows}")
    print(f"[Batch] Ended At: {ended_at.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"[Batch] Elapsed: {ended_at - started_at}")
    print("✅ Daily Summary Job Completed!")


if __name__ == "__main__":
    run_daily_summary_job()
