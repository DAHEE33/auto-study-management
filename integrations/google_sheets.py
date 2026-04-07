import gspread
from oauth2client.service_account import ServiceAccountCredentials
from core.config import settings
import re
from typing import List, Dict

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
        
        # Initialize mock data just in case
        self.mock_data = {
            "Member_Master": [
                {"닉네임": "dev_user", "UserKey": "UK123", "상태": "활동", "목표": "120", "주휴": "1.0", "반휴": "2.0", "월휴": "1", "예치금": "10000"},
            ],
            "Daily_Log": []
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
            
            # Extract spreadsheet key from URL
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

    def append_row(self, sheet_name: str, row_data: List):
        """Append a single row to a specific sheet."""
        if self.is_mock:
            if sheet_name not in self.mock_data:
                self.mock_data[sheet_name] = []
            # We don't have headers mapped well here for generic append, but for mock purposes we just store raw list or dict
            print(f"[MOCK] Appended to {sheet_name}: {row_data}")
            return True
            
        try:
            worksheet = self.spreadsheet.worksheet(sheet_name)
            worksheet.append_row(row_data)
            return True
        except Exception as e:
            print(f"Error appending to sheet {sheet_name}: {e}")
            return False

    def setup_initial_data(self):
        """실제 구글 시트가 비어있을 경우 헤더와 초기 모의 데이터를 시트에 직접 주입합니다."""
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
                    ["닉네임", "UserKey", "상태", "목표", "주휴", "반휴", "월휴", "예치금"],
                    ["dev_user", "UK123", "활동", "120", "1.0", "2.0", "1", "10000"]
                ])
                print("✔️ 'Member_Master' 시트에 기초 데이터 삽입 완료")

            # 2. Daily_Log 세팅
            try:
                ws_log = self.spreadsheet.worksheet("Daily_Log")
            except gspread.exceptions.WorksheetNotFound:
                ws_log = self.spreadsheet.add_worksheet(title="Daily_Log", rows="100", cols="20")
                
            val2 = ws_log.get("A1")
            if not val2 or not val2[0]:
                ws_log.update("A1", [
                    ["날짜", "닉네임", "유형", "제출시각", "OCR시각", "판정", "승인", "벌금", "사진URL"],
                    ["2026-04-06", "dev_user", "일반", "09:20", "23:55", "통과", "-", "0", "https://example.com/mock.jpg"]
                ])
                print("✔️ 'Daily_Log' 시트에 기초 데이터 삽입 완료")
                
        except Exception as e:
            print(f"Error setting up initial data: {e}")

# Singleton instance
sheets_client = GoogleSheetsClient()
