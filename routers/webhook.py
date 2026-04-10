from fastapi import APIRouter, Request
from pydantic import BaseModel
from typing import Dict, Any
from datetime import datetime, timedelta
import httpx
import tempfile
import os
import json

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
                {"messageText": "[🔥 일반 공부 인증]", "action": "message", "label": "일반 공부 인증"},
                {"messageText": "[🌗 반휴 사용]", "action": "message", "label": "반휴 사용"},
                {"messageText": "[🏖️ 주휴/월휴 사용]", "action": "message", "label": "주휴/월휴 사용"},
                {"messageText": "[🏥 특휴(병가/시험) 증빙제출]", "action": "message", "label": "특휴 증빙제출"},
                {"messageText": "[📈 내 현황 확인]", "action": "message", "label": "내 현황 보기"}
            ]
        }
    }

async def download_image(url: str) -> str:
    """URL에서 이미지를 임시 파일로 다운로드 후 경로 반환"""
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        resp.raise_for_status()
        
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
    temp_file.write(resp.content)
    temp_file.close()
    return temp_file.name

def get_user_status(nickname: str) -> str:
    """[내 현황 조회] 통계 조립 (설계 5.1 항목)"""
    members = sheets_client.get_sheet_records("Member_Master")
    daily_logs = sheets_client.get_sheet_records("Daily_Log")
    
    member = next((m for m in members if str(m.get("닉네임", "")) == nickname), None)
    if not member:
        return "❌ 스터디원 정보를 찾을 수 없습니다."
        
    # 날짜 필터링용 기준일
    now = datetime.now()
    week_start = now - timedelta(days=now.weekday()) # 이번주 월요일
    month_start = now.replace(day=1) # 이번달 1일
    
    weekly_studied_mins = 0
    monthly_studied_mins = 0
    weekly_penalty = 0
    
    for log in daily_logs:
        if str(log.get("닉네임", "")) != nickname: continue
        
        try:
            log_date = datetime.strptime(str(log.get("날짜", "")), "%Y-%m-%d")
        except ValueError:
            continue
            
        penalty = int(log.get("벌금액", "0")) if str(log.get("벌금액", "0")).lstrip("-").isdigit() else 0
        
        # 순공시간 합산 (HH:MM 형식 파싱은 여기선 생략하고, 임시로 단순 판독성공 여부만 확인하거나 더미처리 가능) 
        # 구현 편의상 로그 분석보단 예치금과 휴무 잔여량을 집중 표출
        if log_date >= week_start:
            weekly_penalty += penalty
            weekly_studied_mins += 120 # (Mock: 실제로는 공부시간 파싱 로직 필요)
            
        if log_date >= month_start:
            monthly_studied_mins += 120 # (Mock)
            
    deposit = int(str(member.get("예치금", "0")).replace(",", ""))
    remaining_deposit = deposit + weekly_penalty
    
    return (
        f"📊 [{nickname}님의 현황 요약]\n\n"
        f"🔸 남은 주간휴무: {member.get('주간휴무_잔여량', '?')} 회\n"
        f"🔸 남은 월휴: {member.get('남은월휴', '?')} 회\n\n"
        f"🔹 주별 누적 벌금: {weekly_penalty} 원\n"
        f"🔹 남은 예치금: {remaining_deposit} 원\n"
        f"🔹 주별/월별 시간 기록은 대시보드 시트를 통해 더 자세히 확인할 수 있습니다!"
    )

async def process_auth_image(nickname: str, image_url: str, now: datetime, target_override: int, auth_type: str) -> str:
    """인증 처리 공통 로직 (일반/반휴)"""
    try:
        # 1. Admin Calendar 확인
        admin_events = sheets_client.get_sheet_records("Admin_Calendar")
        is_optional = False
        is_shortened = False
        for event in admin_events:
            if str(event.get("날짜", "")) == now.strftime("%Y-%m-%d"):
                if "자율참여" in str(event.get("적용방식", "")):
                    is_optional = True
                elif "단축" in str(event.get("적용방식", "")):
                    is_shortened = True
        
        # 2. 이미지 처리
        local_path = await download_image(image_url)
        drive_url = drive_client.upload_image(local_path, f"{nickname}_{auth_type}_{now.strftime('%Y%m%d%H%M')}.jpg")
        end_time, duration = ocr_service.extract_time_from_image(local_path)
        os.remove(local_path)
        
        if not end_time or not duration:
            return "❌ 시간 정보를 찾지 못했습니다. 타이머가 선명하게 나오도록 다시 찍어주세요!"
            
        # 3. 목표 시간 계산
        members = sheets_client.get_sheet_records("Member_Master")
        base_target = 120
        for m in members:
            if str(m.get("닉네임", "")) == nickname:
                base_target = int(m.get("목표시간(m)", 120))
                break
                
        final_target = target_override if target_override else base_target
        if is_shortened:
            final_target = 60 # 단축 시 1시간 컷
            
        # 4. 검증 및 벌금
        is_valid = engine.validate_dual_time(now, end_time, now)
        penalty = engine.calculate_penalty(duration, final_target) if is_valid else -2000
        
        if is_optional:
            penalty = 0 # 예외일은 벌금 스킵
            
        status_msg = "통과" if is_valid and penalty == 0 else "경고"
        
        # 5. DB 등록
        log_row = [now.strftime("%Y-%m-%d"), nickname, auth_type, status_msg, "-", str(penalty), now.strftime("%H:%M"), drive_url]
        sheets_client.append_row("Daily_Log", log_row)
        
        return f"✅ [{auth_type}] 인증 완료!\n\n- 판정: {status_msg}\n- 적용된 목표시간: {final_target}분\n- 종료시각: {end_time}\n- 공부시간: {duration}\n- 이번 인증 벌금: {penalty}원"

    except Exception as e:
        return f"서버 처리 중 오류가 발생했습니다: {e}"


@router.post("")
async def kakao_webhook(request: Request):
    """카카오톡 채널 챗봇(오픈빌더)으로부터 들어오는 요청을 처리합니다."""
    body = await request.json()
    user_request = body.get("userRequest", {})
    utterance = user_request.get("utterance", "").strip()
    action = body.get("action", {})
    params = action.get("detailParams", {})
    
    print("=== [KAKAO WEBHOOK RECEIVED] ===")
    print(json.dumps(body, indent=2, ensure_ascii=False))
    
    nickname = "dev_user" # 테스트 하드코딩
    now = datetime.now()
    
    # 카카오 파라미터 또는 발화에서 이미지 URL 파싱
    image_url = ""
    for key, value in params.items():
        if isinstance(value, dict) and value.get("origin"):
            origin_val = value["origin"]
            if "http" in origin_val:
                if origin_val.startswith("List(") and origin_val.endswith(")"):
                    origin_val = origin_val[5:-1]
                image_url = origin_val
                break
    
    if not image_url and utterance.startswith("http"):
        image_url = utterance

    # ----------------------------------------------------
    # 분기 처리 로직 (Step 2. 설계문서 매핑)
    # ----------------------------------------------------
    # "반휴사용" 이란 텍스트가 사진과 함께 날아오면 핑퐁 없이 캐치
    block_name = user_request.get("block", {}).get("name", "")
    
    is_half_off = "반휴" in utterance or "반휴" in block_name
    is_week_off = "주휴" in utterance or "월휴" in utterance or "주휴" in block_name
    is_special_off = "특휴" in utterance or "특휴" in block_name
    is_status = "내 현황" in utterance or "현황" in block_name
    
    # 아무 키워드도 매칭 안되고 이미지만 온 경우거나, 오늘인증 관련 키워드인 경우 일반 인증.
    is_normal = (not is_half_off and not is_week_off and not is_special_off and not is_status) and (image_url or "인증" in block_name or "공부" in utterance)

    reply_text = ""

    if is_status:
        # [현황 조회]
        reply_text = get_user_status(nickname)
        
    elif is_week_off:
        # [주휴/월휴] - 사진 불필요, 즉시 차감 및 PASS (DB 차감 로직은 별도 잡에서 처리 고려)
        log_row = [now.strftime("%Y-%m-%d"), nickname, "주휴/월휴", "PASS", "-", "0", now.strftime("%H:%M"), ""]
        sheets_client.append_row("Daily_Log", log_row)
        reply_text = "🏖️ 휴무(주휴/월휴) 처리가 완료되었습니다. 푹 쉬고 오세요! (사진 제출 불필요)"
        
    elif is_special_off:
        # [특휴 증빙]
        if not image_url:
            reply_text = "🏥 특휴 처리를 위해 처방전이나 수험표 등 증빙 사진을 함께 전송해 주세요."
        else:
            try:
                local_path = await download_image(image_url)
                drive_url = drive_client.upload_image(local_path, f"{nickname}_special_{now.strftime('%Y%m%d%H%M')}.jpg")
                os.remove(local_path)
                
                # 승인여부(Pending 상태인 '대기' 추가)
                log_row = [now.strftime("%Y-%m-%d"), nickname, "특휴", "대기", "N", "0", now.strftime("%H:%M"), drive_url]
                sheets_client.append_row("Daily_Log", log_row)
                reply_text = "🏥 특휴 증빙 사진이 정상 접수되었습니다. 관리자 확인 후 '승인' 처리됩니다."
            except Exception as e:
                reply_text = f"업로드 중 에러: {e}"
                
    elif is_half_off:
        # [반휴 사용]
        if not image_url:
            reply_text = "🌗 반휴 적용을 위해 오늘 1h 컷 공부한 구루미 타이머 사진을 같이 전송해 주세요."
        else:
            reply_text = await process_auth_image(nickname, image_url, now, target_override=60, auth_type="반휴")
            
    elif is_normal:
        # [일반 인증]
        if not image_url:
            reply_text = "🔥 타이머가 나오는 사진을 함께 전송해 주셔야 공부 판독이 가능합니다."
        else:
            reply_text = await process_auth_image(nickname, image_url, now, target_override=None, auth_type="일반")
            
    else:
        # 쓰레기 텍스트 등
        reply_text = "말씀하신 내용을 파악하지 못했어요. 아래 메뉴 중 하나를 골라 버튼을 눌러주세요!"

    return build_kakao_response(reply_text)
