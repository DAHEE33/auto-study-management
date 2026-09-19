from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Dict, Tuple


@dataclass(frozen=True)
class OCRTimeValidation:
    valid: bool
    is_past_date: bool = False
    is_absent_due_to_late: bool = False
    is_ontime: bool = False
    reason: str = ""

class CheckInEngine:
    def __init__(self):
        # 12시(정오)를 어제와 오늘의 기준 분기점으로 둡니다.
        self.day_start_hour = 12
        self.deadline_hour = 1

    def get_target_date(self, current_dt: datetime = None) -> str:
        """
        오늘 인정되는 '목표 날짜'를 반환합니다.
        기준: 당일 12:00 ~ 익일 11:59 까지는 동일한 '당일' 데이터로 간주합니다.
        (예: 4월 16일 오전 11시는 4월 15일 자 인증에 속함)
        """
        if current_dt is None:
            current_dt = datetime.now()
            
        if current_dt.hour < self.day_start_hour:
            # 0시 ~ 12시 미만 사이는 전날짜로 귀속
            target = current_dt - timedelta(days=1)
        else:
            target = current_dt
            
        return target.strftime("%Y-%m-%d")

    def is_study_date(self, target_date: str) -> bool:
        """금요일분 익일 제출을 허용하기 위해 접수일 대신 인증 대상일을 검사합니다."""
        return datetime.strptime(target_date, "%Y-%m-%d").weekday() < 5

    def is_blackout_time(self, current_dt: datetime = None) -> bool:
        """
        접수 마감된 절대 휴식 시간 (12:00 ~ 16:59) 인지 확인합니다.
        이 시간대에는 어떠한 인증 / 특휴 / 반주휴 요청도 받지 않습니다.
        """
        if current_dt is None:
            current_dt = datetime.now()
        
        # 02시부터 16:59까지 블랙아웃 (제출 마감은 02시)
        if 2 <= current_dt.hour < 17:
            return True
        return False

    def is_action_allowed(self, action_type: str, current_dt: datetime = None) -> bool:
        """
        액션 종류별 허용 시간 여부를 반환합니다.

        action_type:
        - "status": 항상 허용
        - "general_auth": 일반 인증/반휴 인증 (17:00 ~ 익일 02:00)
        - "week_off": 주휴 처리 (17:00 ~ 익일 12:00)
        - "special_off": 특휴 처리 (17:00 ~ 익일 12:00)
        """
        if current_dt is None:
            current_dt = datetime.now()

        if action_type == "status":
            return True

        if not self.is_study_date(self.get_target_date(current_dt)):
            return False

        hour = current_dt.hour
        if action_type in ("week_off", "month_off", "special_off"):
            return hour >= 17 or hour < 12

        return hour >= 17 or hour < 2

    def validate_ocr_attendance(self, target_date_str: str, end_time_str: str, auth_minutes: int, target_minutes: int) -> OCRTimeValidation:
        """
        OCR로 추출된 종료 시간이 허가된 시간(당일 17:00 ~ 익일 02:00) 안에 안전하게 속하는지 검증합니다.
        Returns: (is_fake_date, is_absent_due_to_late, is_ontime)
        - is_fake_date: 아예 오래전 사진이나 범위 밖 사진인지 여부
        - is_absent_due_to_late: 01:00 ~ 02:00 사이에 끝났으나 '목표 시간'을 달성하지 못한 얄짤없는 결석 케이스
        - is_ontime: 01:00 이전에 정상 종료한 케이스
        """
        try:
            ocr_dt = datetime.strptime(end_time_str, "%Y-%m-%d %H:%M:%S")
            target_base = datetime.strptime(target_date_str, "%Y-%m-%d")
        except (TypeError, ValueError):
            return OCRTimeValidation(False, reason="출석 날짜·시각을 정확히 읽지 못했습니다.")

        valid_start = target_base.replace(hour=17, minute=0, second=0)
        valid_end_exception = (target_base + timedelta(days=1)).replace(hour=2, minute=0, second=0)
        valid_end_normal = (target_base + timedelta(days=1)).replace(hour=1, minute=0, second=0)

        if ocr_dt.date() < target_base.date():
            return OCRTimeValidation(
                False, is_past_date=True,
                reason=f"과거 날짜 사진입니다. (사진 {ocr_dt:%Y-%m-%d} / 대상 {target_date_str})",
            )
        if ocr_dt > valid_end_exception or ocr_dt < valid_start:
            return OCRTimeValidation(False, reason="출석 시각이 인증 허용 범위 밖입니다.")

        # 2. 예외 인정 시간 (01:00 ~ 02:00) 검증 로직
        if ocr_dt > valid_end_normal:
            if auth_minutes < target_minutes:
                # 익일 1시를 넘겼는데 목표를 못 채웠으므로 엄격한 결석 처리
                return OCRTimeValidation(True, is_absent_due_to_late=True)
            else:
                # 1시를 넘겼지만 2시 전이고 목표를 모두 달성했으므로 PASS (기존 ontime 취급)
                return OCRTimeValidation(True, is_ontime=True)

        # 3. 그 외 (17:00 ~ 01:00 이내 종료) -> 완벽한 정상 인증
        return OCRTimeValidation(True, is_ontime=True)

    def validate_ocr_time(self, target_date_str: str, end_time_str: str, auth_minutes: int, target_minutes: int) -> Tuple[bool, bool, bool]:
        """기존 호출부 호환용. 새 코드는 상세 결과를 반환하는 validate_ocr_attendance를 사용합니다."""
        result = self.validate_ocr_attendance(target_date_str, end_time_str, auth_minutes, target_minutes)
        return result.is_past_date, result.is_absent_due_to_late, result.is_ontime

    def is_within_deadline(self, current_dt: datetime = None) -> bool:
        # 하위호환 유지용 (이제 validate_ocr_time이 완전히 대체함)
        return True

    def process_leave_request(self, user_record: Dict, leave_type: str) -> Tuple[bool, str, float]:
        """
        휴무 요청을 검증합니다.
        leave_type: '주휴', '반휴', '월휴'
        리턴: (승인여부-bool, 챗봇출력메시지-str, 차감량-float)
        """
        # 구글 시트에서 넘어온 데이터 파싱
        weekly_leave_str = str(user_record.get("주간휴무", "0"))
        monthly_leave_str = str(user_record.get("남은월휴", "0"))
        
        try:
            weekly_leave = float(weekly_leave_str)
        except ValueError:
            weekly_leave = 0.0
            
        try:
            monthly_leave = float(monthly_leave_str) 
        except ValueError:
            monthly_leave = 0.0

        if leave_type == "반휴":
            if weekly_leave < 0.5:
                return False, "잔여 주간 휴무가 부족합니다. 이번 주 휴무를 모두 사용하셨습니다.", 0.0
            return True, "반휴가 적용되었습니다. 오늘 목표 시간은 1시간으로 고정됩니다.\n사진을 전송해 주세요!", 0.5
            
        elif leave_type == "주휴":
            if weekly_leave < 1.0:
                return False, "잔여 주간 휴무가 부족합니다. 이번 주 휴무를 모두 사용하셨습니다.", 0.0
            return True, "주휴 처리가 완료되었습니다. 오늘 하루 푹 쉬세요! (자동 PASS)", 1.0
            
        elif leave_type == "월휴":
            if monthly_leave < 1.0:
                return False, "남은 월휴가 없습니다.", 0.0
            return True, "월휴 처리가 완료되었습니다. 푹 쉬세요! (자동 PASS)", 1.0
        
        return False, f"알 수 없는 휴가 타입입니다: {leave_type}", 0.0

check_in_engine = CheckInEngine()
