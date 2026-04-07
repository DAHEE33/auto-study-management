import os
from dotenv import load_dotenv
from pathlib import Path

# Load .env file
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

class Settings:
    GOOGLE_SHEET_URL: str = os.getenv("GOOGLE_SHEET_URL", "")
    CREDENTIALS_FILE: str = os.getenv("CREDENTIALS_FILE", "credentials.json")
    GOOGLE_DRIVE_FOLDER_ID: str = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")

    @property
    def credentials_path(self) -> Path:
        return Path(__file__).parent.parent / self.CREDENTIALS_FILE

settings = Settings()
