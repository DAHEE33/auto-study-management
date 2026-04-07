from fastapi import APIRouter, Request
from pydantic import BaseModel
from typing import Dict, Any

router = APIRouter(prefix="/webhook", tags=["Webhook"])

def build_kakao_response(text: str) -> Dict[str, Any]:
    """카카오 i 챗봇 스펙에 맞춘 심플한 텍스트 응답 제네레이터"""
    return {
        "version": "2.0",
        "template": {
            "outputs": [
                {
                    "simpleText": {
                        "text": text
                    }
                }
            ],
            "quickReplies": [
                {
                    "messageText": "[🔥 오늘 인증]",
                    "action": "message",
                    "label": "오늘 인증하기"
                },
                {
                    "messageText": "[📈 내 현황]",
                    "action": "message",
                    "label": "내 현황 보기"
                }
            ]
        }
    }

@router.post("")
async def kakao_webhook(request: Request):
    """
    카카오톡 채널 챗봇(오픈빌더)으로부터 들어오는 요청을 처리합니다.
    """
    body = await request.json()
    
    # 1. 사용자 발화내용 추출
    user_request = body.get("userRequest", {})
    utterance = user_request.get("utterance", "").strip()
    
    # 카카오톡 유저 식별자 (필요 시 활용)
    user_id = user_request.get("user", {}).get("id", "UnknownUser")
    
    # 2. 분기 처리 (기획서 명시된 Quick Reply 커맨드)
    if "[🔥 오늘 인증]" in utterance:
        reply_text = "사진을 업로드 해 주세요! (OCR 판독 로직은 차후 이미지 핸들러에 연결됩니다.)"
        
    elif "[📈 내 현황]" in utterance:
        # TODO: Member_Master 시트 조회 로직 연결
        reply_text = f"현재 주간 누적 시간을 계산중입니다.\n(ID: {user_id})"
        
    else:
        # 이미지 업로드 혹은 일반 텍스트
        reply_text = "말씀하신 내용을 알아듣지 못했어요.\n아래 퀵메뉴를 이용해 주세요!"

    return build_kakao_response(reply_text)
