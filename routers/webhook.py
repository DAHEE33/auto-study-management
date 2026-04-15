from fastapi import APIRouter, Request, BackgroundTasks
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
from services.settlement_engine import settlement_engine
from services.check_in_engine import check_in_engine

router = APIRouter(prefix="/webhook", tags=["Webhook"])

def build_kakao_response(text: str) -> Dict[str, Any]:
    """카카오 i 챗봇 스펙에 맞춘 심플한 텍스트 응답 제네레이터"""
    return {
        "version": "2.0",
        "template": {
            "outputs": [{"simpleText": {"text": text}}],
            "quickReplies": [
                {"messageText": "인증", "action": "message", "label": "🔥 일반 인증"},
                {"messageText": "반휴 인증", "action": "message", "label": "🌗 반휴 사용"},
                {"messageText": "주휴 사용", "action": "message", "label": "🏖️ 주휴 사용"},
                {"messageText": "월휴 사용", "action": "message", "label": "🏖️ 월휴 사용"},
                {"messageText": "특휴 증빙하기", "action": "message", "label": "🏥 특휴 신청"},
                {"messageText": "내 현황", "action": "message", "label": "📈 내 현황 확인"}
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

@router.post("")
async def kakao_webhook(request: Request, background_tasks: BackgroundTasks):
    """카카오톡 채널 챗봇(오픈빌더)으로부터 들어오는 요청을 처리합니다."""
    body = await request.json()
    user_request = body.get("userRequest", {})
    utterance = user_request.get("utterance", "").strip()
    action = body.get("action", {})
    params = action.get("detailParams", {})
    
    # 1. UserKey 추출 및 멤버 확보
    userkey = user_request.get("user", {}).get("id", "")
    member_record = sheets_client.get_member_by_userkey(userkey)
    
    if not member_record:
        return build_kakao_response(f"❌ 승인되지 않은 사용자입니다.\nUserKey: {userkey}\n카카오톡 채널 방장에게 위 UserKey를 캡처해서 신규 멤버 등록을 요청해 주세요.")
        
    nickname = member_record.get("닉네임", "Unkown")
    row_idx = member_record.get("_row_index", -1)
    
    # 2. 파라미터 또는 발화에서 이미지 URL 파싱
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

    now = datetime.now()
    target_date = check_in_engine.get_target_date(now)

    # 3. 사용자 발화(또는 Block명)로 인증/휴무 종류 분기 처리
    block_name = user_request.get("block", {}).get("name", "")
    
    is_half_off = "반휴" in utterance or "반휴" in block_name
    is_week_off = "주휴" in utterance or "주휴" in block_name
    is_month_off = "월휴" in utterance or "월휴" in block_name
    is_special_off = "특휴" in utterance or "특휴" in block_name
    is_status = "내 현황" in utterance or "현황" in block_name

    reply_text = ""

    if is_status:
        # [현황 조회]
        deposit = member_record.get("예치금", "0")
        w_leave = member_record.get("주간휴무", "0")
        m_leave = member_record.get("남은월휴", "0")
        
        reply_text = (
            f"📊 [{nickname}님의 현황 요약]\n\n"
            f"🔸 남은 주간휴무: {w_leave} 회\n"
            f"🔸 남은 월휴: {m_leave} 회\n"
            f"🔹 남은 예치금: {deposit} 원\n\n"
            f"📌 주별/월별 상세 통계는 구글 시트를 통해 확인이 가능합니다."
        )

    elif is_week_off or is_month_off:
        # [주휴 / 월휴] (버튼 클릭만으로 완료되는 로직)
        leave_type = "주휴" if is_week_off else "월휴"
        is_approved, msg, deduct_amt = check_in_engine.process_leave_request(member_record, leave_type)
        
        if is_approved:
            col_idx = 6 if leave_type == "주휴" else 7 # 6: 주간휴무, 7: 남은월휴
            old_val_str = member_record.get("주간휴무" if leave_type == "주휴" else "남은월휴", "0")
            new_val = max(0.0, float(old_val_str) - deduct_amt)
            
            # DB 잔여량 차감 반영
            sheets_client.update_cell("Member_Master", row_idx, col_idx, new_val)
            
            # 로그 반영
            log_row = [target_date, nickname, leave_type, "PASS", "-", "0", "-", "0", "-"]
            sheets_client.append_row("Daily_Log", log_row)
            
        reply_text = msg

    elif is_special_off:
        # [특휴 요청] - 관리자 승인 대기
        if not image_url:
            reply_text = "🏥 특휴 처리를 위해 처방전이나 수험표 등의 증빙 사진을 함께 전송해 주세요."
        else:
            try:
                local_path = await download_image(image_url)
                drive_url = drive_client.upload_image(local_path, f"{nickname}_special_{now.strftime('%Y%m%d%H%M')}.jpg")
                os.remove(local_path)
                
                # '대기', 승인여부 'N'
                log_row = [target_date, nickname, "특휴", "대기", "N", "-", "-", "0", drive_url]
                sheets_client.append_row("Daily_Log", log_row)
                reply_text = "🏥 특휴 증빙 사진이 정상 접수되었습니다. 방장 확인(승인) 전까지는 대기 상태가 유지됩니다."
            except Exception as e:
                reply_text = f"이미지 업로드 중 에러 발생: {e}"

    else:
        # [일반 인증 / 반휴 인증] (이미지가 들어왔거나 요청하는 경우)
        if not image_url:
            if is_half_off:
                reply_text = "🌗 반휴 적용을 위해 오늘 최소 1시간을 달성한 구루미 타이머 사진을 같이 전송해 주세요. (예: 캡처 전송 시 텍스트로 '반휴' 입력)"
            else:
                reply_text = "🔥 타이머와 누적시간이 잘 보이는 [구루미 메인 화면] 캡처 사진을 전송해 주셔야 공부 판독이 가능합니다."
        else:
            auth_type = "반휴" if is_half_off else "일반"
            target_override = None
            
            # 반휴일 경우 우선 잔여휴무 검증
            if auth_type == "반휴":
                is_approved, msg, deduct_amt = check_in_engine.process_leave_request(member_record, "반휴")
                if not is_approved:
                    return build_kakao_response(msg)
                
                target_override = 1 # 반휴는 목표 1시간으로 고정
                col_idx = 6 # 주간휴무 컬럼 
                new_val = max(0.0, float(member_record.get("주간휴무", "0")) - deduct_amt)
                sheets_client.update_cell("Member_Master", row_idx, col_idx, new_val)

            # 이미지 파싱 프로세스 (추후 5초 타임아웃 회피를 위해 큐 혹은 콜백 설계 고려)
            try:
                # deadline 위반 여부 판별 (01시 이후 ~ 05시 이전 전송 시 지각)
                is_ontime = check_in_engine.is_within_deadline(now)
                
                local_path = await download_image(image_url)
                drive_url = drive_client.upload_image(local_path, f"{nickname}_{auth_type}_{now.strftime('%Y%m%d%H%M')}.jpg")
                
                # OCR 서비스가 (종료시각, 당일공부시간(분), 누적시간(분)) 튜플 반환
                ocr_result = ocr_service.extract_time_from_image(local_path)
                os.remove(local_path)
                
                end_time, duration, total_mnts = ocr_result[0], ocr_result[1], ocr_result[2]
                
                if not end_time or duration == 0:
                    reply_text = "❌ OCR 엔진이 시간 정보를 찾지 못했습니다. 숫자가 선명하게 나오도록 자르지 말고 다시 찍어주세요!"
                else:
                    base_target = int(member_record.get("목표시간", 120))
                    final_target = target_override * 60 if target_override else base_target
                    
                    # 벌금 계산 (거짓/데이터조작 여부 로직은 별도로 OCR 누적시간 연동 필요)
                    is_fake_time = False 
                    is_fake_date = False
                    
                    penalty = settlement_engine.calculate_penalty(final_target, duration, not is_ontime, is_fake_time, is_fake_date)
                    status_msg = "PASS" if penalty == 0 and is_ontime else ("경고(벌금)" if is_ontime else "지각전송(심사요망)")
                    
                    # 당일시간(종류), 사진누적(분->시간)
                    dur_str = f"{duration//60}시간 {duration%60}분"
                    tot_str = f"{total_mnts//60}시간 {total_mnts%60}분"
                    
                    # DB 로그 (Daily_Log: 날짜, 닉네임, 종류, 판정, 승인여부, 당일시간, 사진누적, 벌금액, 증빙)
                    log_row = [target_date, nickname, auth_type, status_msg, "-", dur_str, tot_str, str(penalty), drive_url]
                    sheets_client.append_row("Daily_Log", log_row)
                    
                    reply_text = (
                        f"✅ [{auth_type}] 인증 제출이 완료되었습니다!\n\n"
                        f"- 판정결과: {status_msg}\n"
                        f"- 적용 목표시간: {final_target//60}시간 {final_target%60}분\n"
                        f"- 당일 공부시간: {dur_str}\n"
                        f"- 누적 공부시간: {tot_str}\n"
                        f"- 규정 내 전송: {'네 (01시 이전)' if is_ontime else '아니오 (지각, 추후 벌금 심사)'}\n"
                        f"- 이번 인증 벌금: {penalty}원"
                    )

            except Exception as e:
                reply_text = f"서버 처리 중 오류가 발생했습니다: {e}"

    return build_kakao_response(reply_text)
