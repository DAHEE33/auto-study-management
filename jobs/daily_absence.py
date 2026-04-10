import sys
from pathlib import Path
from datetime import datetime, timedelta

# 프로젝트 루트 경로 추가 (모듈 import 위함)
sys.path.append(str(Path(__file__).parent.parent))

from integrations.google_sheets import sheets_client

def run_daily_absence_job():
    """
    [매일 12:00 정오 실행]
    전날(기준일) 스터디 로그를 확인하여 결석자 판정 및 휴가 차감/벌금 부과를 수행합니다.
    """
    print("🚀 [Batch] Starting Daily Absence Check Job...")
    
    # 1. 기준일: 어제 (자정까지 진행되는 스터디 특성상, 오늘 정오에 확인하는 로그는 '어제' 날짜)
    target_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"Target Date: {target_date}")
    
    # 2. 멤버 리스트 및 로그 조회
    members = sheets_client.get_sheet_records("Member_Master")
    daily_logs = sheets_client.get_sheet_records("Daily_Log")
    
    # 활동 중인 멤버 필터링
    active_members = [m for m in members if str(m.get("상태", "")) == "활동"]
    
    # 기준일 로그에 제출 기록이 있는 사람 찾기
    submitted_users = set()
    for log in daily_logs:
        if str(log.get("날짜", "")) == target_date:
            submitted_users.add(str(log.get("닉네임", "")))
            
    # 2.5 Admin Calendar 자율참여 예외 확인
    admin_events = sheets_client.get_sheet_records("Admin_Calendar")
    is_optional_day = False
    for event in admin_events:
        if str(event.get("날짜", "")) == target_date:
            if "자율참여" in str(event.get("적용방식", "")):
                is_optional_day = True
                break
                
    if is_optional_day:
        print(f"🟢 {target_date} 일자는 Admin_Calendar 에 의해 [자율참여] 로 지정되었습니다.")
        print("🟢 결석 벌금을 매기지 않고 Job을 정상 종료합니다.")
        return
            
    # 3. 결석자 판정 및 휴가 차감 로직 구현
    for member in active_members:
        nickname = str(member.get("닉네임", ""))
        
        if nickname not in submitted_users:
            print(f"⚠️ [{nickname}] 님은 {target_date} 로그 제출 기록이 없습니다. (결석 의심)")
            
            # (실제 로직) Daily_Log 시트에 결석(벌금 -2000) 기록을 진짜 밀어넣습니다!
            penalty_row = [target_date, nickname, "결석", "-", "-", "결석", "-", "-2000", "-"]
            sheets_client.append_row("Daily_Log", penalty_row)
            print(f"   -> ✔️ [처리 완료] {nickname}님 결석 벌금(-2000) 기록을 구글 시트에 강제 작성했습니다.")

    print("✅ Daily Absence Check Job Completed!")

if __name__ == "__main__":
    run_daily_absence_job()
