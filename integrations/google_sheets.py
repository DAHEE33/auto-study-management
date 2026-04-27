import gspread
from oauth2client.service_account import ServiceAccountCredentials
from core.config import settings
import re
from typing import List, Dict, Optional

class GoogleSheetsClient:
    def __init__(self):
        self.scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive.file",
            "https://www.googleapis.com/auth/drive"
        ]
        self.client = None
        self.spreadsheet = None
        self.is_mock = False
        
        # 최신 설계 기준 모의 데이터
        self.mock_data = {
            "Member_Master": [
                {"닉네임": "dev_user", "UserKey": "UK123", "상태": "활동", "목표시간": "120", "최종누적": "15600", "주간휴무": "1.0", "남은월휴": "1", "예치금": "10000", "비고": "-", "남은특휴": "1"},
            ],
            "Daily_Log": [],
            "Admin_Config": [{"날짜": "2026-05-01", "이벤트 타입": "특휴개수", "목표시간 조정": "0", "주간 공지사항 (추가 멘트)": "-", "월별특휴개수": "3"}]
        }

        try:
            if not settings.credentials_path.exists():
                print("⚠️ Credentials file not found. Running in MOCK mode.")
                self.is_mock = True
                return

            creds = ServiceAccountCredentials.from_json_keyfile_name(
                settings.credentials_path, self.scope
            )
            self.client = gspread.authorize(creds)
            
            # 주소에서 Spreadsheet 키 추출
            match = re.search(r'/d/([a-zA-Z0-9-_]+)', settings.GOOGLE_SHEET_URL)
            if match:
                sheet_key = match.group(1)
                self.spreadsheet = self.client.open_by_key(sheet_key)
            else:
                raise ValueError("Invalid Google Sheet URL")
            
        except Exception as e:
            print(f"⚠️ Failed to initialize Google Sheets client: {e}. Running in MOCK mode.")
            self.is_mock = True

    def get_sheet_records(self, sheet_name: str) -> List[Dict]:
        """Fetch all records from a specific sheet as a list of dictionaries."""
        if self.is_mock:
            return self.mock_data.get(sheet_name, [])
            
        try:
            worksheet = self.spreadsheet.worksheet(sheet_name)
            return worksheet.get_all_records()
        except Exception as e:
            print(f"Error fetching sheet {sheet_name}: {e}")
            return []

    def get_member_by_userkey(self, userkey: str) -> Optional[Dict]:
        """UserKey를 사용하여 Member_Master에서 유저 정보를 조회하고, 시트 Row Index도 함께 반환합니다."""
        records = self.get_sheet_records("Member_Master")
        for idx, row in enumerate(records):
            if str(row.get("UserKey", "")) == userkey:
                row["_row_index"] = idx + 2  # 헤더가 1번 행이므로 +2
                return row
        return None

    def append_row(self, sheet_name: str, row_data: List):
        """Append a single row to a specific sheet."""
        if self.is_mock:
            if sheet_name not in self.mock_data:
                self.mock_data[sheet_name] = []
            print(f"[MOCK] Appended to {sheet_name}: {row_data}")
            return True
            
        try:
            worksheet = self.spreadsheet.worksheet(sheet_name)
            worksheet.append_row(row_data)
            return True
        except Exception as e:
            print(f"Error appending to sheet {sheet_name}: {e}")
            return False

    def upsert_daily_log(self, row_data: List):
        """
        Daily_Log 시트에 대해 같은 날짜, 같은 닉네임이 존재하면 해당 행을 덮어쓰고,
        존재하지 않으면 맨 아래에 새 행을 추가(append)합니다.
        row_data: [Date, Nickname, Type, Result, Approval, DailyTime, TotalTime, Penalty, ImageID]
        """
        if self.is_mock:
            print(f"[MOCK] Upserted to Daily_Log: {row_data}")
            return True
            
        try:
            target_date = str(row_data[0])
            target_nickname = str(row_data[1])
            
            worksheet = self.spreadsheet.worksheet("Daily_Log")
            records = worksheet.get_all_records()
            
            for idx, row in enumerate(records):
                if str(row.get("날짜", "")) == target_date and str(row.get("닉네임", "")) == target_nickname:
                    # 일치하는 행을 찾음 (헤더가 1번 행이므로 데이터의 첫 번째 줄 인덱스는 2)
                    row_idx = idx + 2
                    
                    # 해당 범위(A열~마지막 열) 덮어쓰기
                    end_col_letter = chr(ord('A') + len(row_data) - 1)
                    range_addr = f"A{row_idx}:{end_col_letter}{row_idx}"
                    worksheet.update(range_addr, [row_data])
                    print(f"🔄 Daily_Log 행 덮어쓰기(Override) 완료: {target_nickname} ({target_date})")
                    return True
                    
            # 일치하는 행이 없으면 추가
            worksheet.append_row(row_data)
            print(f"➕ Daily_Log 새 행 추가 완료: {target_nickname} ({target_date})")
            return True
            
        except Exception as e:
            print(f"Error upserting to Daily_Log: {e}")
            return False

    def update_cell(self, sheet_name: str, row: int, col: int, val):
        """특정 셀 업데이트 (잔여 휴무 수량 차감 등에 사용)"""
        if self.is_mock:
            print(f"[MOCK] Update {sheet_name} R{row}C{col} -> {val}")
            return True

        try:
            worksheet = self.spreadsheet.worksheet(sheet_name)
            worksheet.update_cell(row, col, val)
            return True
        except Exception as e:
            print(f"Error updating cell in {sheet_name}: {e}")
            return False

    def get_daily_penalty(self, target_date: str, nickname: str) -> int:
        """당일 기록된 벌금이 있다면 반환합니다 (주휴/월휴로 덮어쓸 때 환불용)"""
        if self.is_mock:
            return 0
        try:
            worksheet = self.spreadsheet.worksheet("Daily_Log")
            records = worksheet.get_all_records()
            for row in records:
                if str(row.get("날짜", "")) == target_date and str(row.get("닉네임", "")) == nickname:
                    try:
                        return int(str(row.get("벌금액", "0")).replace(",", ""))
                    except ValueError:
                        return 0
            return 0
        except Exception as e:
            print(f"Error fetching daily penalty: {e}")
            return 0

    def get_today_auth_history(self, target_date: str, nickname: str) -> dict:
        """오늘 이미 인증한 내역이 있는지 조회 (재인증 판별용)"""
        if self.is_mock:
            return {}
        try:
            worksheet = self.spreadsheet.worksheet("Daily_Log")
            records = worksheet.get_all_records()
            for row in records:
                if str(row.get("날짜", "")) == target_date and str(row.get("닉네임", "")) == nickname:
                    # 기존 인증 시간 파싱 (예: "1시간 32분" -> 92분)
                    dur_str = str(row.get("당일시간", ""))
                    prev_duration = 0
                    if "시간" in dur_str or "분" in dur_str:
                        h = 0
                        m = 0
                        if "시간" in dur_str:
                            h_str = dur_str.split("시간")[0].strip()
                            h = int(h_str) if h_str.isdigit() else 0
                        if "분" in dur_str:
                            m_str = dur_str.split("시간")[-1].replace("분", "").strip()
                            m = int(m_str) if m_str.isdigit() else 0
                        prev_duration = h * 60 + m
                        
                    return {
                        "prev_duration": prev_duration,
                        "prev_status": str(row.get("판정", ""))
                    }
            return {}
        except Exception as e:
            print(f"Error fetching today auth history: {e}")
            return {}

    def setup_initial_data(self):
        """실제 구글 시트가 비어있을 경우 헤더와 초기 데이터를 최신 아키텍처 기준으로 주입합니다."""
        if self.is_mock or not self.spreadsheet:
            return
            
        try:
            # 1. Member_Master 세팅
            try:
                ws_member = self.spreadsheet.worksheet("Member_Master")
            except gspread.exceptions.WorksheetNotFound:
                ws_member = self.spreadsheet.add_worksheet(title="Member_Master", rows="100", cols="20")
                
            val1 = ws_member.get("A1")
            if not val1 or not val1[0]:
                ws_member.update("A1", [
                    ["닉네임", "UserKey", "상태", "목표시간", "최종누적", "주간휴무", "남은월휴", "예치금", "비고", "남은특휴"],
                    ["dev_user", "UK123", "활동", "120", "15,600", "1.0", "1", "10,000", "-", "1"]
                ])
                print("✔️ 'Member_Master' 시트에 기초 데이터 삽입 완료")
            else:
                member_headers = ws_member.row_values(1)
                if "남은특휴" not in member_headers:
                    ws_member.add_cols(1)
                    member_headers.append("남은특휴")
                    ws_member.update("A1:J1", [member_headers])
                    records = ws_member.get_all_records()
                    for idx, row in enumerate(records, start=2):
                        current_val = str(row.get("남은특휴", "")).strip()
                        if not current_val:
                            ws_member.update_cell(idx, 10, "1")
                    print("✔️ 'Member_Master' 시트에 '남은특휴' 컬럼 추가 완료")

            # 2. Daily_Log 세팅
            try:
                ws_log = self.spreadsheet.worksheet("Daily_Log")
            except gspread.exceptions.WorksheetNotFound:
                ws_log = self.spreadsheet.add_worksheet(title="Daily_Log", rows="100", cols="20")
                
            val2 = ws_log.get("A1")
            if not val2 or not val2[0]:
                ws_log.update("A1", [
                    ["날짜", "닉네임", "유형", "판정", "승인여부(특휴시)", "당일시간", "사진누적", "벌금액", "이미지ID"],
                    ["2026-04-15", "dev_user", "일반", "PASS", "-", "135", "15,600", "0", "drive_id_1"]
                ])
                print("✔️ 'Daily_Log' 시트에 기초 데이터 삽입 완료")

            # 3. Admin_Config 세팅
            try:
                ws_admin = self.spreadsheet.worksheet("Admin_Config")
            except gspread.exceptions.WorksheetNotFound:
                ws_admin = self.spreadsheet.add_worksheet(title="Admin_Config", rows="50", cols="5")
                
            val3 = ws_admin.get("A1")
            if not val3 or not val3[0]:
                ws_admin.update("A1", [
                    ["날짜", "이벤트 타입", "목표시간 조정", "주간 공지사항 (추가 멘트)", "월별특휴개수"],
                    ["2026-05-05", "자율참여", "0", "어린이날 즐겁게 보내세요!", "-"],
                    ["2026-05-01", "특휴개수", "0", "-", "3"]
                ])
                print("✔️ 'Admin_Config' 시트에 기초 데이터 삽입 완료")
            else:
                admin_headers = ws_admin.row_values(1)
                if "월별특휴개수" not in admin_headers:
                    ws_admin.add_cols(1)
                    admin_headers.append("월별특휴개수")
                    ws_admin.update("A1:E1", [admin_headers])
                    records = ws_admin.get_all_records()
                    for idx, row in enumerate(records, start=2):
                        current_val = str(row.get("월별특휴개수", "")).strip()
                        if not current_val:
                            ws_admin.update_cell(idx, 5, "-")
                    print("✔️ 'Admin_Config' 시트에 '월별특휴개수' 컬럼 추가 완료")
                
        except Exception as e:
            print(f"Error setting up initial data: {e}")

# Singleton instance
sheets_client = GoogleSheetsClient()
