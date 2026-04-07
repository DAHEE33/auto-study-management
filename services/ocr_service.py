import os
import re
from google.cloud import vision
from core.config import settings
from typing import Optional, Tuple

class OCRService:
    def __init__(self):
        self.is_mock = False
        self.client = None
        
        try:
            # Set environment variable for Google Cloud SDK auth
            if settings.credentials_path.exists():
                os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(settings.credentials_path)
                self.client = vision.ImageAnnotatorClient()
            else:
                self.is_mock = True
        except Exception as e:
            print(f"⚠️ Google Cloud Vision init failed: {e}. Running in MOCK Mode.")
            self.is_mock = True

    def extract_time_from_image(self, image_path: str) -> Tuple[Optional[str], Optional[str]]:
        """
        [MOCK/REAL] 구루미 UI 이미지에서 텍스트를 파싱하여 공부 종료 시각과 순공 시간을 추출합니다.
        Returns:
            (종료시각 HH:MM, 순공시간 HH:MM)
        """
        if self.is_mock:
            # Mock 데이터 반환 (테스트용)
            print(f"[MOCK] OCR 추출 진행: {image_path}")
            return "23:55", "02:30"
            
        try:
            with open(image_path, "rb") as image_file:
                content = image_file.read()

            image = vision.Image(content=content)
            response = self.client.text_detection(image=image)
            texts = response.text_annotations
            
            if not texts:
                return None, None
                
            full_text = texts[0].description
            
            # TODO: 실제 구루미 화면에 맞춰 고도화된 정규식 패턴 매칭 필요
            # 예를 들어 "23:55\n02:30:00" 형태의 패턴을 찾는다고 가정.
            # 지금은 간단히 HH:MM 형식의 가장 마지막 시간을 종료 시간으로,
            # HH:MM:SS 형식을 공부 시간으로 유추하는 샘플 로직.
            
            time_pattern = re.findall(r'\b([0-1]?[0-9]|2[0-3]):([0-5][0-9])\b', full_text)
            duration_pattern = re.findall(r'\b([0-9]{1,2}):([0-5][0-9]):([0-5][0-9])\b', full_text)
            
            end_time = f"{time_pattern[-1][0]}:{time_pattern[-1][1]}" if time_pattern else None
            duration = f"{duration_pattern[0][0]}:{duration_pattern[0][1]}" if duration_pattern else None
            
            return end_time, duration
            
        except Exception as e:
            print(f"OCR Error: {e}")
            return None, None

ocr_service = OCRService()
