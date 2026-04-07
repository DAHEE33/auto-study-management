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

# Singleton instance
drive_client = GoogleDriveClient()
