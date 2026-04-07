import sys
from pathlib import Path

# 프로젝트 루트 참조
sys.path.append(str(Path(__file__).parent.parent))
from integrations.google_sheets import sheets_client

def check_admin_approvals():
    """
    [Admin Control]
    관리자가 구글 시트 GUI에서 특휴 증빙을 보고 상태를 변경했을 때,
    이를 감지하여 유저에게 카톡으로 승인 완료 알림을 보내는 시뮬레이션입니다.
    """
    print("🛡️ [Admin Sync] Checking for Administrator actions...")
    
    logs = sheets_client.get_sheet_records("Daily_Log")
    
    # "판정"이 'Pending' 이었다가, 관리자가 직접 구글 시트에서 '승인(Approved)'으로 체크박스를 바꾼 행을 찾습니다.
    # 지금은 샘플로 상태를 읽어와 검사하는 로직을 나열합니다.
    pending_found = False
    
    for idx, log in enumerate(logs):
        판정 = str(log.get("판정", ""))
        승인 = str(log.get("승인", ""))
        
        # 기획서 7.1 로직 구현
        if 판정 == "Pending" and 승인 == "TRUE":
            pending_found = True
            nickname = log.get("닉네임", "Unknown")
            print(f"   🎉 [감지] 관리자가 '{nickname}'님의 사유를 방금 승인했습니다!")
            print(f"      -> 카카오톡 알림톡 전송: '{nickname}님, 특수 휴가가 관리자에 의해 승인되었습니다!'")
            
            # TODO: 다시 알림이 가지 않도록 해당 행(row)의 '판정'을 'Completed'로 구글 시트에 즉시 덮어쓰기 업데이트
            # row_index = idx + 2 (header + 0-index)
            # sheets_client.update_cell("Daily_Log", row_index, 판정_열_위치, "Completed")
            
    if not pending_found:
        print("   -> 현재 관리자 승인이 진행된 새로운 건이 없습니다.")

    print("✅ Admin Sync Job Checked!")

if __name__ == "__main__":
    check_admin_approvals()
