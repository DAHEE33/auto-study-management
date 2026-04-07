from datetime import datetime, timedelta

class SettlementEngine:
    """
    스터디 정산 및 시간 검증 핵심 비즈니스 로직
    """

    def __init__(self):
        # 벌금 기준표
        self.FEE_MILD = 500   # 목표 미달이나 1h 이상
        self.FEE_SEVERE = 1000 # 1h 미만 수행 또는 부정/오류
        self.FEE_ABSENT = 2000 # 결석 (휴가 부족 시)

    def validate_dual_time(self, server_submit_time: datetime, ocr_end_time_str: str, target_date: datetime) -> bool:
        """
        [이중 시간 검증 (Dual-Time Validation)]
        1. 제출 시각 (Server Time): target_date 익일 12:00 정오 마감
        2. 공부 종료 시각 (OCR Time): target_date 익일 01:00 이전 종료 필수
        
        Args:
            server_submit_time: 봇이 사용자로부터 이미지를 전송받은 실제 시각
            ocr_end_time_str: "23:55" 과 같은 OCR 추출 문자열
            target_date: 인증을 하려는 목표 일자 (보통 오늘)
            
        Returns:
            정상 여부 (True/False)
        """
        try:
            # 1. 서버 제출 시각 검증 로직 (익일 12:00 이전)
            next_day_noon = target_date.replace(hour=12, minute=0, second=0, microsecond=0) + timedelta(days=1)
            is_submit_valid = server_submit_time <= next_day_noon
            
            if not is_submit_valid:
                return False

            # 2. OCR 종료 시각 검증 로직 ("HH:MM" 파싱 후 당일 01:00 이전인지 확인)
            # 종료 시연: "23:55" 이거나 자정을 넘긴 "00:30" 일 수 있음.
            hours, minutes = map(int, ocr_end_time_str.split(":"))
            
            # 주의: "00:XX"는 실제로는 target_date의 '익일'이지만 스터디 룰상 당일분위 취급.
            # 01:00 이전인지 확인
            is_ocr_valid = False
            if hours >= 1: # 01:xx ~ 23:xx
                # 자정 이후(당일 01시~23시) 스터디는 정상 카운트 (보통 02시에 끝내면 탈락)
                # 문서 기준 "당일 01:00 이전 종료 필수" -> 23:55은 당연히 통과.
                # 새벽에 일찍 시작하는 사람은? (04:00 시작) -> 이건 예외 케이스 처리 필요 가능.
                # 우선 문서대로 심플하게 01시를 넘기면 실패, 01~04시는 불가능 영역으로 가정.
                if hours >= 4: # 예: 오전 4시 이후 ~ 23시 59분까지 공부 종료 시 통과
                    is_ocr_valid = True
                else:
                    is_ocr_valid = False
            else:
                # hours == 0 (00:00 ~ 00:59) -> 새벽 종료 인정
                is_ocr_valid = True

            return is_submit_valid and is_ocr_valid
            
        except Exception as e:
            print(f"Time validation error: {e}")
            return False

    def calculate_penalty(self, duration_str: str, target_minutes: int) -> int:
        """
        [벌금 산정 로직]
        Args:
            duration_str: "02:30" 같은 총 공부 시간
            target_minutes: Member_Master에 정의된 목표 공부 시간(분)
            
        Returns:
            부과될 벌금액 (int, 마이너스 또는 0)
        """
        if not duration_str:
            # 판독 불가 (부정/오류)
            return -self.FEE_SEVERE
            
        try:
            h, m = map(int, duration_str.split(":"))
            total_studied_minutes = (h * 60) + m
            
            if total_studied_minutes >= target_minutes:
                return 0 # 목표 100% 달성 벌금 없음
            elif total_studied_minutes >= 60:
                # 1시간(60분) 이상은 했으나 목표 미달
                return -self.FEE_MILD
            else:
                # 1시간(60분) 미만 심각 미달
                return -self.FEE_SEVERE
                
        except Exception:
            return -self.FEE_SEVERE

# Singleton
engine = SettlementEngine()
