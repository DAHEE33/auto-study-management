from fastapi import APIRouter, Request
from pydantic import BaseModel
from typing import Dict, Any
from datetime import datetime
import httpx
import tempfile
import os

from integrations.google_sheets import sheets_client
from integrations.google_drive import drive_client
from services.ocr_service import ocr_service
from services.settlement_engine import engine

router = APIRouter(prefix="/webhook", tags=["Webhook"])

def build_kakao_response(text: str) -> Dict[str, Any]:
    """카카오 i 챗봇 스펙에 맞춘 심플한 텍스트 응답 제네레이터"""
    return {
        "version": "2.0",
        "template": {
            "outputs": [{"simpleText": {"text": text}}],
            "quickReplies": [
                {"messageText": "[🔥 오늘 인증]", "action": "message", "label": "오늘 인증하기"},
                {"messageText": "[📈 내 현황]", "action": "message", "label": "내 현황 보기"}
            ]
        }
    }

async def download_image(url: str) -> str:
    """URL에서 이미지를 임시 파일로 다운로드 후 경로 반환"""
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        resp.raise_for_status()
        
    # 임시 파일 생성
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
    temp_file.write(resp.content)
    temp_file.close()
    return temp_file.name

@router.post("")
async def kakao_webhook(request: Request):
    """
    카카오톡 채널 챗봇(오픈빌더)으로부터 들어오는 요청을 처리합니다.
    """
    body = await request.json()
    
    user_request = body.get("userRequest", {})
    utterance = user_request.get("utterance", "").strip()
    action = body.get("action", {})
    params = action.get("detailParams", {})
    
    # 디버깅을 위해 콘솔에 페이로드 출력
    import json
    print("=== [KAKAO WEBHOOK RECEIVED] ===")
    print(json.dumps(body, indent=2, ensure_ascii=False))
    
    # 임시로 유저 아이디를 닉네임으로 사용 (실제로는 매핑 필요)
    # 구글 시트에 사전 등록된 dev_user 로 하드코딩 (E2E 테스트 목적)
    nickname = "dev_user"
    now = datetime.now()
    
    # 이미지 URL 추출 (새로운 로직)
    image_url = ""
    # 1. detailParams에서 확인 (sys.secureimage 등 설정된 모든 파라미터를 순회)
    for key, value in params.items():
        if isinstance(value, dict) and value.get("origin"):
            origin_val = value["origin"]
            if "http" in origin_val:
                # 카카오 secureimage는 종종 'List(http://...)' 형태로 들어옵니다.
                if origin_val.startswith("List(") and origin_val.endswith(")"):
                    origin_val = origin_val[5:-1]
                image_url = origin_val
                break
    
    # 2. utterance가 http URL 자체인 경우
    if not image_url and utterance.startswith("http"):
        image_url = utterance

    # 사용자가 인증 버튼을 눌렀거나, 사진(이미지) 자체를 보냈거나, 블록 이름에 인증이 포함된 경우
    block_name = user_request.get("block", {}).get("name", "")
    is_auth_intent = "[🔥 오늘 인증]" in utterance or image_url or "인증" in block_name

    if is_auth_intent:
        if not image_url:
            return build_kakao_response("인증 사진을 함께 업로드해 주셔야 판독이 가능합니다! 카카오톡 카메라/앨범에서 사진을 전송해주세요.")

        reply_text = f"이미지를 수신했습니다. 잠시만 기다려주세요...\n(분석 중...)"


        
        try:
            # 1. 이미지 다운로드 및 구글 드라이브 백업
            local_path = await download_image(image_url)
            drive_url = drive_client.upload_image(local_path, f"{nickname}_{now.strftime('%Y%m%d%H%M')}.jpg")
            
            # 2. OCR 파싱 (공부종료/순공시간 추출)
            end_time, duration = ocr_service.extract_time_from_image(local_path)
            
            # 3. 비즈니스 로직(Dual-Time) 및 벌금 산정
            if end_time and duration:
                # 닉네임 바탕으로 Member_Master 조회해 목표 시간 파악
                members = sheets_client.get_sheet_records("Member_Master")
                target_mins = 120 # 기본값
                for m in members:
                    if m.get("닉네임") == nickname:
                        target_mins = int(m.get("목표", 120))
                        break
                        
                is_valid = engine.validate_dual_time(now, end_time, now)
                penalty = engine.calculate_penalty(duration, target_mins) if is_valid else -2000
                
                status_msg = "통과" if is_valid and penalty == 0 else "경고"
                
                # 4. Daily_Log 에 등재
                log_row = [now.strftime("%Y-%m-%d"), nickname, "일반", now.strftime("%H:%M"), end_time, status_msg, "-", str(penalty), drive_url]
                sheets_client.append_row("Daily_Log", log_row)
                
                reply_text = f"✅ 인증 완료!\n\n- 판정: {status_msg}\n- 종료시각: {end_time}\n- 공부시간: {duration}\n- 이번 인증 벌금: {penalty}원\n\n시트에 기록되었습니다!"

            else:
                reply_text = "❌ 이미지에서 시간 정보를 찾지 못했습니다. 사진을 더 선명하게 다시 찍어주세요!"
                
            os.remove(local_path) # 임시 파일 삭제

        except Exception as e:
            reply_text = f"서버 처리 중 오류가 발생했습니다: {e}"
        
    elif "[📈 내 현황]" in utterance:
        # 간단한 현황 조회
        reply_text = f"현재 {nickname}님의 현황입니다.\n대시보드 주소에서 자세한 내용을 확인하세요!"
        
    else:
        reply_text = "말씀하신 내용을 알아듣지 못했어요.\n아래 메뉴 중 하나를 선택해 주세요."

    return build_kakao_response(reply_text)
