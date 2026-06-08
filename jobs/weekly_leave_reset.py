import sys
from pathlib import Path
from datetime import datetime

# 프로젝트 루트 경로 추가 (모듈 import 위함)
sys.path.append(str(Path(__file__).parent.parent))

from services.leave_reset_service import leave_reset_service


def run_weekly_leave_reset_job():
    """
    [매주 월요일 17:00(KST) 실행용]
    활동 멤버의 주간휴무를 1로 초기화합니다.
    LeaveResetService 내부 마커(Admin_Config)로 중복 실행을 방지합니다.
    """
    started_at = datetime.now()
    print("🚀 [Batch] Starting Weekly Leave Reset Job...")
    print(f"[Batch] Started At: {started_at.strftime('%Y-%m-%d %H:%M:%S')}")

    leave_reset_service.run_if_needed(now=started_at)

    ended_at = datetime.now()
    print(f"[Batch] Ended At: {ended_at.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"[Batch] Elapsed: {ended_at - started_at}")
    print("✅ Weekly Leave Reset Job Completed!")


if __name__ == "__main__":
    run_weekly_leave_reset_job()
