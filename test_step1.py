import sys
from pathlib import Path

# Add project root to sys.path so we can import modules properly
sys.path.append(str(Path(__file__).parent))

from core.config import settings
from integrations.google_sheets import sheets_client
from integrations.google_drive import drive_client

def test_config():
    print("--- Config Test ---")
    print(f"SHEET URL: {settings.GOOGLE_SHEET_URL}")
    print(f"DRIVE FOLDER: {settings.GOOGLE_DRIVE_FOLDER_ID}")
    print(f"CREDENTIALS: {settings.credentials_path} (exists: {settings.credentials_path.exists()})")

def test_sheets():
    print("\n--- Google Sheets Test ---")
    if sheets_client.is_mock:
        print("[Status: MOCK MODE]")
    else:
        print("[Status: REAL MODE]")
        # 실제 시트가 비어있다면 헤더와 샘플 데이터를 넣습니다.
        sheets_client.setup_initial_data()
        
    members = sheets_client.get_sheet_records("Member_Master")
    print(f"Fetched Members: {members}")

def test_drive():
    print("\n--- Google Drive Test ---")
    if drive_client.is_mock:
        print("[Status: MOCK MODE]")
    else:
        print("[Status: REAL MODE]")
    
    # Just checking initialization. Upload requires a real file.
    print("Drive client initialized successfully.")

if __name__ == "__main__":
    test_config()
    test_sheets()
    test_drive()
