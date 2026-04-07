import sys
from pathlib import Path
from datetime import datetime, timedelta

sys.path.append(str(Path(__file__).parent))

from services.ocr_service import ocr_service
from services.settlement_engine import engine

def test_ocr():
    print("--- [OCR Service Test] ---")
    end_time, duration = ocr_service.extract_time_from_image("dummy_path.jpg")
    print(f"Extracted End Time: {end_time}")
    print(f"Extracted Duration: {duration}")

def test_dual_time():
    print("\n--- [Dual-Time Validation Test] ---")
    target_date = datetime(2026, 4, 7) # 오늘 목표 일자
    
    # 시나리오 1: 정상 (23시 55분 종료, 정오 이전 제출)
    valid_submit = datetime(2026, 4, 8, 10, 0, 0)
    res1 = engine.validate_dual_time(valid_submit, "23:55", target_date)
    print(f"Scenario 1 (Normal 23:55, Submitted 10:00 AM next day): Pass={res1}")

    # 시나리오 2: 실패 (정오 지나서 제출)
    late_submit = datetime(2026, 4, 8, 13, 0, 0)
    res2 = engine.validate_dual_time(late_submit, "23:55", target_date)
    print(f"Scenario 2 (Late Submit 1:00 PM next day): Pass={res2}")

    # 시나리오 3: 한계 (자정을 넘어 00시 30분 종료, 정상 제출)
    res3 = engine.validate_dual_time(valid_submit, "00:30", target_date)
    print(f"Scenario 3 (Dawn 00:30 End, Normal Submit): Pass={res3}")
    
    # 시나리오 4: 실패 (새벽 2시 종료 -> 01:00 이후이므로 실패)
    res4 = engine.validate_dual_time(valid_submit, "02:00", target_date)
    print(f"Scenario 4 (Too Late Dawn 02:00 End): Pass={res4}")

def test_penalty():
    print("\n--- [Penalty Calculation Test] ---")
    target_mins = 120 # 2시간 목표
    
    # 1. 100% 달성 (2시간 30분)
    p1 = engine.calculate_penalty("02:30", target_mins)
    print(f"2h 30m / Output: {p1}원 (Expected: 0)")
    
    # 2. 1시간 이상 목표 미달 (1시간 30분)
    p2 = engine.calculate_penalty("01:30", target_mins)
    print(f"1h 30m / Output: {p2}원 (Expected: -500)")
    
    # 3. 1시간 미만 심각 미달 (0시간 45분)
    p3 = engine.calculate_penalty("00:45", target_mins)
    print(f"0h 45m / Output: {p3}원 (Expected: -1000)")
    
    # 4. 부정/오류 처리
    p4 = engine.calculate_penalty(None, target_mins)
    print(f"Error parse / Output: {p4}원 (Expected: -1000)")

if __name__ == "__main__":
    test_ocr()
    test_dual_time()
    test_penalty()
