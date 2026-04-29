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
    started_at = datetime.now()
    print("🚀 [Batch] Starting Daily Absence Check Job...")
    print(f"[Batch] Started At: {started_at.strftime('%Y-%m-%d %H:%M:%S')}")
    
    # 1. 대상 날짜 (서버는 매일 정오에 어제 날짜를 정산함)
    target_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"Target Date: {target_date}")
    
    # 2. 휴일(자율참여) 여부 확인
    admin_events = sheets_client.get_sheet_records("Admin_Config")
    is_optional_day = False
    for event in admin_events:
        if str(event.get("날짜", "")).strip() == target_date:
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
    print(f"[Batch] Active Members: {len(active_members)}")
    
    submitted_users = set()
    for log in daily_logs:
        if str(log.get("날짜", "")) == target_date:
            submitted_users.add(str(log.get("닉네임", "")))
            
    print(f"[Batch] Submitted Users: {len(submitted_users)}")

    # 4. 결석자 판정, DB 기록 및 예치금 자동 차감
    processed_absent = 0
    failed_updates = 0
    for idx, member in enumerate(members):
        if str(member.get("상태", "")) != "활동":
            continue
            
        nickname = str(member.get("닉네임", ""))
        
        if nickname not in submitted_users:
            print(f"⚠️ [{nickname}] 님은 {target_date} 로그 유효 기록이 없습니다. (결석 처리)")
            
            # Daily Log 기록
            penalty_row = [target_date, nickname, "결석", "-", "-", "0시간 0분", "0시간 0분", "-2000", "-"]
            append_ok = sheets_client.append_row("Daily_Log", penalty_row)
            if not append_ok:
                failed_updates += 1
                print(f"❌ [Batch] Daily_Log append 실패: nickname={nickname}, date={target_date}")
                continue
            
            # 예치금 차감 반영 (Member_Master)
            old_deposit_str = str(member.get("예치금", "0")).replace(",", "")
            old_deposit = int(old_deposit_str) if old_deposit_str.replace("-", "").isdigit() else 0
            new_deposit = old_deposit - 2000
            
            row_idx = idx + 2 # 헤더 보정
            update_ok = sheets_client.update_cell("Member_Master", row_idx, 8, str(new_deposit)) # H열이 예치금(8번째)
            if not update_ok:
                failed_updates += 1
                print(f"❌ [Batch] 예치금 차감 실패: nickname={nickname}, row={row_idx}, old={old_deposit}, new={new_deposit}")
                continue
            
            print(f"   -> ✔️ [처리 완료] {nickname}님 결석(-2000) 기록 확정 및 예치금 차감 완료")
            processed_absent += 1

    ended_at = datetime.now()
    print(f"[Batch] Processed Absences: {processed_absent}")
    print(f"[Batch] Failed Updates: {failed_updates}")
    print(f"[Batch] Ended At: {ended_at.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"[Batch] Elapsed: {ended_at - started_at}")
    print("✅ Daily Absence Check Job Completed!")

if __name__ == "__main__":
    run_daily_absence_job()
