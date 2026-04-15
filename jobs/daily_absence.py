import sys
from pathlib import Path
from datetime import datetime, timedelta

# 프로젝트 루트 경로 추가 (모듈 import 위함)
sys.path.append(str(Path(__file__).parent.parent))

from integrations.google_sheets import sheets_client

def run_daily_absence_job():
    """
    [매일 정오(12:00) 실행용]
    어제 날짜를 기준으로 인증 기록이 없는 '활동' 상태 멤버에게 결석(-2000원)을 부과합니다.
    자율참여(공휴일)일 경우 결석 처리를 건너뜁니다.
    """
    print("🚀 [Batch] Starting Daily Absence Check Job...")
    
    # 1. 대상 날짜 (서버는 매일 정오에 어제 날짜를 정산함)
    target_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"Target Date: {target_date}")
    
    # 2. 휴일(자율참여) 여부 확인
    admin_events = sheets_client.get_sheet_records("Admin_Config")
    is_optional_day = False
    for event in admin_events:
        if str(event.get("날짜", "")) == target_date:
            if "자율참여" in str(event.get("이벤트 타입", "")):
                is_optional_day = True
                break
                
    if is_optional_day:
        print(f"🟢 {target_date} 일자는 Admin_Config 에 의해 [자율참여] 로 지정되었습니다.")
        print("🟢 결석 벌금을 매기지 않고 배치 작업을 정상 종료합니다.")
        return

    # 3. 멤버 및 어제 자 로그 조회
    members = sheets_client.get_sheet_records("Member_Master")
    daily_logs = sheets_client.get_sheet_records("Daily_Log")
    
    active_members = [m for m in members if str(m.get("상태", "")) == "활동"]
    
    submitted_users = set()
    for log in daily_logs:
        if str(log.get("날짜", "")) == target_date:
            submitted_users.add(str(log.get("닉네임", "")))
            
    # 4. 결석자 판정 및 DB 기록 ("날짜", "닉네임", "유형", "판정", "승인여부(특휴시)", "당일시간", "사진누적", "벌금액", "이미지ID")
    for member in active_members:
        nickname = str(member.get("닉네임", ""))
        
        if nickname not in submitted_users:
            print(f"⚠️ [{nickname}] 님은 {target_date} 로그 유효 기록이 없습니다. (결석 처리)")
            
            penalty_row = [target_date, nickname, "결석", "-", "-", "0시간 0분", "0시간 0분", "-2000", "-"]
            sheets_client.append_row("Daily_Log", penalty_row)
            
            print(f"   -> ✔️ [처리 완료] {nickname}님 결석 벌금(-2000) 기록 작성")

    print("✅ Daily Absence Check Job Completed!")

if __name__ == "__main__":
    run_daily_absence_job()
