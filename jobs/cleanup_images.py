import sys
from pathlib import Path

# 프로젝트 루트 경로 추가 (모듈 import 위함)
sys.path.append(str(Path(__file__).parent.parent))

from integrations.google_drive import drive_client

def run_cleanup_job():
    """
    [매주 일요일 실행용]
    최대 보관 기한인 14일이 지난 인증 이미지를 구글 드라이브에서 완전 삭제합니다.
    (용량 최적화 목적 - Zero Storage Policy)
    """
    print("🚀 [Batch] Starting Drive Storage Cleanup Job...")
    
    # 14일 초과 파일 삭제 실행
    deleted_count = drive_client.delete_files_older_than(days=14)
    
    print(f"✅ Drive Cleanup Job Completed! 총 {deleted_count}개의 오래된 인증 사진이 삭제되었습니다.")

if __name__ == "__main__":
    run_cleanup_job()
