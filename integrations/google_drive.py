from google.oauth2 import service_account
import googleapiclient.discovery
import googleapiclient.http
from core.config import settings
import os
import uuid

class GoogleDriveClient:
    def __init__(self):
        self.scope = ["https://www.googleapis.com/auth/drive"]
        self.service = None
        self.is_mock = False
        
        try:
            if not settings.credentials_path.exists():
                print("⚠️ Credentials file not found. Running Drive in MOCK mode.")
                self.is_mock = True
                return

            creds = service_account.Credentials.from_service_account_file(
                settings.credentials_path, scopes=self.scope
            )
            self.service = googleapiclient.discovery.build('drive', 'v3', credentials=creds)
            
        except Exception as e:
            print(f"⚠️ Failed to initialize Google Drive client: {e}. Running in MOCK mode.")
            self.is_mock = True

    def upload_image(self, file_path: str, filename: str) -> str:
        """Uploads a file to Google Drive and returns the webViewLink (URL)."""
        if self.is_mock:
            print(f"[MOCK] Uploaded {filename} to Google Drive.")
            return f"https://mock.drive.google.com/view/{uuid.uuid4()}"
            
        try:
            file_metadata = {
                'name': filename,
                'parents': [settings.GOOGLE_DRIVE_FOLDER_ID]
            }
            # Simplistic mime type assumption for images, can be extended
            media = googleapiclient.http.MediaFileUpload(file_path, mimetype='image/jpeg', resumable=True)
            
            file = self.service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id, webViewLink'
            ).execute()
            
            return file.get('webViewLink')
        except Exception as e:
            print(f"Error uploading file to Drive: {e}")
            return ""

    def delete_files_older_than(self, days: int) -> int:
        """지정된 일수(days)보다 오래된 구글 드라이브 내 사진을 영구 삭제합니다."""
        if self.is_mock:
            print(f"[MOCK] {days}일이 지난 파일들을 삭제했다고 가정합니다.")
            return 0
            
        try:
            from datetime import datetime, timedelta
            threshold_date = (datetime.utcnow() - timedelta(days=days)).isoformat() + "Z"
            
            # 특정 폴더 내에서 수정된 지 X일이 지난 파일 쿼리 (휴지통 제외)
            query = f"'{settings.GOOGLE_DRIVE_FOLDER_ID}' in parents and modifiedTime < '{threshold_date}' and trashed = false"
            
            response = self.service.files().list(q=query, fields="files(id, name)").execute()
            files = response.get('files', [])
            
            deleted_count = 0
            for file in files:
                self.service.files().delete(fileId=file['id']).execute()
                print(f"🗑️ 오래된 인증 사진 삭제 완료: {file.get('name')}")
                deleted_count += 1
                
            return deleted_count
        except Exception as e:
            print(f"구글 드라이브 파일 삭제 중 에러 발생: {e}")
            return 0

# Singleton instance
drive_client = GoogleDriveClient()
