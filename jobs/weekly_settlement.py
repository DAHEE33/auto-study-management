import sys
from pathlib import Path
from datetime import datetime, timedelta

# 프로젝트 루트 경로 추가
sys.path.append(str(Path(__file__).parent.parent))

from integrations.google_sheets import sheets_client
from services.settlement_engine import settlement_engine

def run_weekly_settlement_job():
    """
    [매주 토요일 12:00 정오 실행용]
    최근 7일(지난주 토 오전 5시 ~ 오늘 금 밤)간의 Daily_Log를 집계하여
    벌금의 총합과 성실 멤버 1/n 보상을 계산한 문자열 템플릿을 생성하고,
    관리자가 구글 시트에서 복사하기 쉽게 Admin_Config에 저장합니다.
    """
    print("🚀 [Batch] Starting Weekly Settlement Job...")
    
    # 1. 정산 대상 날짜 산정 (보통 토요일에 실행하므로 이번주 토요일을 기준으로 최근 7일)
    now = datetime.now()
    end_date_obj = now - timedelta(days=1)  # 금요일
    start_date_obj = end_date_obj - timedelta(days=6) # 지난주 토요일
    
    start_date = start_date_obj.strftime("%Y-%m-%d")
    end_date = end_date_obj.strftime("%Y-%m-%d")
    print(f"기간: {start_date} ~ {end_date}")
    
    # 2. 데이터 가져오기
    members = sheets_client.get_sheet_records("Member_Master")
    daily_logs = sheets_client.get_sheet_records("Daily_Log")
    
    # 기간 내 로그 필터링
    filtered_logs = []
    for log in daily_logs:
        val = str(log.get("날짜", ""))
        try:
            log_d = datetime.strptime(val, "%Y-%m-%d")
            # 시작일 <= 로그일 <= 종료일
            if start_date_obj.date() <= log_d.date() <= end_date_obj.date():
                filtered_logs.append(log)
        except ValueError:
            pass
            
    # 3. 정산 리포트 텍스트 생성
    report_text = settlement_engine.generate_weekly_report(
        start_date=start_date,
        end_date=end_date,
        daily_logs=filtered_logs,
        master_members=members,
        admin_notice="이번 주도 모두 고생하셨습니다! 다음 주 월요일까지 정산액을 개인 입금/차감해 주세요."
    )
    
    print("\n" + "="*40)
    print(report_text)
    print("="*40 + "\n")
    
    # 4. 방장용 시트(Admin_Config)에 자동 저장
    # 방장이 시트를 열람하고 복사/붙여넣기 편하도록
    log_row = [now.strftime("%Y-%m-%d"), "정산리포트", "-", report_text]
    sheets_client.append_row("Admin_Config", log_row)
    
    print("✅ Weekly Settlement Job Completed! (리포트가 Admin_Config 시트에 등록되었습니다.)")

if __name__ == "__main__":
    run_weekly_settlement_job()
