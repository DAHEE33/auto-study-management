from datetime import datetime, timedelta
from typing import Dict, Tuple

class CheckInEngine:
    def __init__(self):
        # 5시를 새벽 기준 시간으로 둡니다.
        self.day_start_hour = 5
        self.deadline_hour = 1

    def get_target_date(self, current_dt: datetime = None) -> str:
        """
        오늘 인정되는 '목표 날짜'를 반환합니다.
        기준: 당일 05:00 ~ 익일 04:59 까지는 동일한 '당일' 데이터로 간주합니다.
        (예: 4월 15일 새벽 2시는 4월 14일 자 인증에 속함)
        """
        if current_dt is None:
            current_dt = datetime.now()
            
        if current_dt.hour < self.day_start_hour:
            # 0시 ~ 5시 사이는 전날짜로 귀속
            target = current_dt - timedelta(days=1)
        else:
            target = current_dt
            
        return target.strftime("%Y-%m-%d")

    def is_within_deadline(self, current_dt: datetime = None) -> bool:
        """
        제출 시각이 정상 마감 기한인 '당일 05:00 ~ 익일 01:00' 이내인지 판별.
        리턴값: True(정시에 사진 발송함), False(01:00 초과 지각 발송함)
        """
        if current_dt is None:
            current_dt = datetime.now()
            
        if current_dt.hour == 0:
            # 00:00 ~ 00:59 까지는 인정 (익일 01:00 마감이므로)
            return True
            
        if 1 <= current_dt.hour < self.day_start_hour:
            # 01:00 ~ 04:59 는 1시 마감 초과이므로 명백한 규칙 위반 발송 시점.
            # (벌금 판독 대상으로 강하게 검사할 필요가 있음)
            return False
            
        return True # 05:00 ~ 23:59

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
