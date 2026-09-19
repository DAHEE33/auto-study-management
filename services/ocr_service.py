import os
import re
from dataclasses import dataclass
from typing import Optional

from google.cloud import vision

from core.config import settings


TIMESTAMP_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\b")
DURATION_RE = re.compile(r"(?:(\d+)\s*시간(?:\s*(\d+)\s*분)?|(\d+)\s*분)")


@dataclass(frozen=True)
class OCRResult:
    attendance_at: Optional[str]
    daily_minutes: Optional[int]
    total_minutes: Optional[int]
    full_text: str
    error: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


class OCRService:
    def __init__(self):
        self.client = None
        try:
            if settings.credentials_path.exists():
                os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(settings.credentials_path)
                self.client = vision.ImageAnnotatorClient()
        except Exception as exc:
            # 초기화 실패를 성공형 mock 데이터로 바꾸지 않습니다.
            print(f"⚠️ Google Cloud Vision init failed: {type(exc).__name__}")

    @staticmethod
    def _duration_minutes(match: re.Match) -> int:
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2) or match.group(3) or 0)
        return hours * 60 + minutes

    @staticmethod
    def _normalize_nickname(value: str) -> str:
        return re.sub(r"\s+", "", value).casefold()

    @classmethod
    def parse_attendance_rows(cls, rows: list[str], nickname: str, full_text: str = "") -> OCRResult:
        """좌표로 조립된 각 가로 행에서 닉네임→시각→당일→누적 순서를 검증합니다."""
        wanted = cls._normalize_nickname(nickname)
        matches = []
        for row in rows:
            timestamp = TIMESTAMP_RE.search(row)
            if not timestamp:
                continue

            nickname_area = row[:timestamp.start()].strip(" |\t")
            if not nickname_area or wanted not in cls._normalize_nickname(nickname_area):
                continue

            durations = list(DURATION_RE.finditer(row, timestamp.end()))
            if len(durations) != 2:
                continue

            matches.append((
                timestamp.group(1),
                cls._duration_minutes(durations[0]),
                cls._duration_minutes(durations[1]),
            ))

        if len(matches) != 1:
            reason = "닉네임과 필수 열이 일치하는 행을 찾지 못했습니다."
            if len(matches) > 1:
                reason = "닉네임과 일치하는 출석표 행이 여러 개입니다."
            return OCRResult(None, None, None, full_text, reason)

        attendance_at, daily, total = matches[0]
        return OCRResult(attendance_at, daily, total, full_text)

    @staticmethod
    def _rows_from_annotations(annotations) -> list[str]:
        words = []
        for annotation in annotations:
            vertices = getattr(getattr(annotation, "bounding_poly", None), "vertices", None)
            if not vertices:
                continue
            xs = [getattr(vertex, "x", 0) or 0 for vertex in vertices]
            ys = [getattr(vertex, "y", 0) or 0 for vertex in vertices]
            words.append({
                "text": str(getattr(annotation, "description", "")).strip(),
                "x": min(xs),
                "cy": (min(ys) + max(ys)) / 2,
                "height": max(1, max(ys) - min(ys)),
            })

        lines = []
        for word in sorted(words, key=lambda item: (item["cy"], item["x"])):
            line = next(
                (candidate for candidate in lines
                 if abs(candidate["cy"] - word["cy"]) <= max(candidate["height"], word["height"]) * 0.65),
                None,
            )
            if line is None:
                lines.append({"cy": word["cy"], "height": word["height"], "words": [word]})
            else:
                line["words"].append(word)
                count = len(line["words"])
                line["cy"] = ((line["cy"] * (count - 1)) + word["cy"]) / count
                line["height"] = max(line["height"], word["height"])

        return [
            " ".join(word["text"] for word in sorted(line["words"], key=lambda item: item["x"]))
            for line in sorted(lines, key=lambda item: item["cy"])
        ]

    def extract_time_from_image(self, image_path: str, nickname: str) -> OCRResult:
        if self.client is None:
            return OCRResult(None, None, None, "", "OCR 서비스를 초기화하지 못했습니다.")

        try:
            with open(image_path, "rb") as image_file:
                response = self.client.text_detection(image=vision.Image(content=image_file.read()))

            if getattr(response, "error", None) and response.error.message:
                return OCRResult(None, None, None, "", "OCR 처리에 실패했습니다.")

            annotations = response.text_annotations
            if not annotations:
                return OCRResult(None, None, None, "", "사진에서 문자를 찾지 못했습니다.")

            full_text = annotations[0].description
            rows = self._rows_from_annotations(annotations[1:])
            return self.parse_attendance_rows(rows, nickname, full_text)
        except Exception as exc:
            print(f"OCR Error: {type(exc).__name__}")
            return OCRResult(None, None, None, "", "OCR 처리 중 오류가 발생했습니다.")


ocr_service = OCRService()
