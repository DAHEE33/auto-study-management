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

    def _parse_duration_to_minutes(self, text: str) -> int:
        """'X시간 Y분' 또는 'X시간' 또는 'Y분' 형태의 텍스트를 파싱하여 분 단위로 반환"""
        match = re.search(r'(?:(\d+)시간)?\s*(?:(\d+)분)?', text)
        if not match:
            return 0
        h = int(match.group(1) or 0)
        m = int(match.group(2) or 0)
        return h * 60 + m

    def extract_time_from_image(self, image_path: str) -> Tuple[Optional[str], int, int, str]:
        """
        구루미 UI 이미지에서 텍스트를 파싱하여 공부 종료 시각과 순공 시간을 추출합니다.
        Returns:
            (종료시각 "YYYY-MM-DD HH:MM:SS" 또는 "HH:MM", 당일시간(분), 누적시간(분), OCR원문텍스트)
        """
        if self.is_mock:
            # Mock 데이터 반환 (테스트용)
            print(f"[MOCK] OCR 추출 진행: {image_path}")
            return "23:55", 120, 8550, "dev_user 2시간 142시간 30분 2026-04-15 20:49:02"
            
        try:
            with open(image_path, "rb") as image_file:
                content = image_file.read()

            image = vision.Image(content=content)
            # OCR 엔진 호출
            response = self.client.text_detection(image=image)
            texts = response.text_annotations
            
            if not texts:
                return None, 0, 0
                
            full_text = texts[0].description
            
            # 1. 종료 시각(타임스탬프) 파싱
            # 보통 "2026-04-15 20:49:02" 포맷을 띌 것이라 가정 (수정된 스펙 기준)
            # 타임스탬프가 없으면 HH:MM 단독 포맷 대비.
            dt_match = re.search(r'\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}', full_text)
            if dt_match:
                end_time = dt_match.group(0)
            else:
                hm_match = re.findall(r'\b([0-1]?[0-9]|2[0-3]):([0-5][0-9])\b', full_text)
                end_time = f"{hm_match[-1][0]}:{hm_match[-1][1]}" if hm_match else None

            # 2. 당일시간, 누적시간 파싱 ("X시간 Y분" 형태)
            durations = re.findall(r'(\d+시간(?:\s*\d+분)?|\d+분)', full_text)
            
            daily_mnts = 0
            total_mnts = 0
            
            if len(durations) == 0:
                pass
            elif len(durations) == 1:
                # 하나만 인식된 경우 (보통 당일시간일 확률이 높음)
                daily_mnts = self._parse_duration_to_minutes(durations[0])
            else:
                # 두 개 이상 인식되었을 경우, 당일시간과 누적시간 구분
                # 일반적으로 누적시간이 물리적으로 더 큼
                val1 = self._parse_duration_to_minutes(durations[0])
                val2 = self._parse_duration_to_minutes(durations[1])
                daily_mnts = min(val1, val2)
                total_mnts = max(val1, val2)
            
            return end_time, daily_mnts, total_mnts, full_text
            
        except Exception as e:
            print(f"OCR Error: {e}")
            return None, 0, 0, ""

ocr_service = OCRService()
