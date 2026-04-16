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

# 봇이 사용자 요청 맥락을 기억하기 위한 상태 저장소 (메모리 방식)
# 형태: { "UserKey": {"type": "반휴" | "특휴", "expires": datetime_object} }
user_states = {}

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
                {"messageText": "특휴 증빙하기", "action": "message", "label": "🏥 특휴 신청"},
                {"messageText": "내 현황", "action": "message", "label": "📈 내 현황 확인"},
                {"messageText": "목표 변경", "action": "message", "label": "🎯 목표시간 변경"}
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
    
    # 📝 [로그 출력] 챗봇이 보낸 UserKey를 서버 터미널에서 즉시 확인합니다.
    print(f"\n================ [카카오 웹훅 수신] ================")
    print(f"► 유저키(UserKey): {userkey}")
    print(f"► 수신 텍스트(utterance): {utterance}")
    print(f"====================================================\n")

    # [신규 기능 1: 기기 연동] "등록 산들바람" 
    if utterance.startswith("등록 "):
        target_nick = utterance.replace("등록 ", "").strip()
        records = sheets_client.get_sheet_records("Member_Master")
        for idx, row in enumerate(records):
            if str(row.get("닉네임", "")) == target_nick:
                row_idx = idx + 2
                # UserKey는 B열(2)
                sheets_client.update_cell("Member_Master", row_idx, 2, userkey)
                return build_kakao_response(f"✅ '{target_nick}'님의 기기가 정상적으로 연동되었습니다! 이제 인증이 가능합니다.")
        return build_kakao_response(f"❌ 엑셀에 '{target_nick}' 이라는 닉네임이 존재하지 않습니다. 방장님께 먼저 닉네임 추가를 요청하세요.")

    member_record = sheets_client.get_member_by_userkey(userkey)
    
    if not member_record:
        return build_kakao_response(f"❌ 승인되지 않은 사용자입니다.\nUserKey: {userkey}\n카카오톡 채널 방장에게 위 UserKey를 캡처해서 신규 멤버 등록을 요청해 주세요.\n(또는 '등록 [닉네임]' 을 쳐서 자동 연동하세요)")
        
    nickname = member_record.get("닉네임", "Unkown")
    row_idx = member_record.get("_row_index", -1)

    # [신규 기능 2: 목표 시간 변경] "목표변경 3시간" 또는 버튼 클릭
    if utterance.startswith("목표변경") or "목표 변경" in utterance:
        import re
        nums = re.findall(r'\d+', utterance)
        if not nums:
            return build_kakao_response("❌ 죄송합니다. 형식이 올바르지 않습니다. 다시 입력해주세요.\n(예시: 목표변경 3시간, 목표변경 240)")
        
        new_target_minutes = int(nums[0]) * 60 if "시간" in utterance else int(nums[0])
        # 목표시간은 D열(4)
        sheets_client.update_cell("Member_Master", row_idx, 4, str(new_target_minutes))
        return build_kakao_response(f"✅ 목표 시간이 '{new_target_minutes//60}시간 {new_target_minutes%60}분'으로 변경 적용되었습니다!")
    
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

    # 💡 [블랙아웃 체크 (낮 12:00 ~ 16:59 차단)]
    # 현황 조회를 제외한 모든 인증/신청/처리는 거부
    if check_in_engine.is_blackout_time(now) and not is_status:
        return build_kakao_response("❌ 처리 기간이 지났습니다.\n(당일 인증 및 휴가 처리는 17:00 부터 접수를 받습니다.)")

    # 💡 [휴무일(자율참여) 우선 차단]
    admin_events = sheets_client.get_sheet_records("Admin_Config")
    is_optional_day = False
    for event in admin_events:
        if str(event.get("날짜", "")).strip() == target_date:
            if "자율참여" in str(event.get("이벤트 타입", "")):
                is_optional_day = True
                break
                
    if is_optional_day and not is_status:
        return build_kakao_response("🏖️ 오늘은 [자율참여(휴무일)] 지정일입니다!\n\n거짓 인증, 휴가(반휴/주휴) 차감 등 일체의 스터디 인증이 필요하지 않습니다. 마음 편히 쉬시거나 자율적으로 공부해주세요! 🎉")

    # 💡 [State 조회 및 적용] 
    # 사진만 보냈더라도, 10분 내에 누른 버튼(특휴/반휴)이 있다면 해당 상태로 강제 지정합니다.
    state = user_states.get(userkey)
    if state and state["expires"] > now:
        if state["type"] == "반휴":
            is_half_off = True
        elif state["type"] == "특휴":
            is_special_off = True
        
        # 실제로 사진이 들어와서 인증 처리가 시작되면, 대기 상태를 소진(삭제)합니다.
        if image_url:
            del user_states[userkey]

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
            
            # 로그 반영 (당일 기록 Override 적용)
            log_row = [target_date, nickname, leave_type, "PASS", "-", "0", "-", "0", "-"]
            sheets_client.upsert_daily_log(log_row)
            
        reply_text = msg

    elif is_special_off:
        # [특휴 요청] - 관리자 승인 대기
        if not image_url:
            # 특휴를 누르고 아직 사진을 안 보냈으므로 상태 기억!
            user_states[userkey] = {"type": "특휴", "expires": now + timedelta(minutes=10)}
            reply_text = "🏥 특휴 처리를 위해 처방전이나 수험표 등의 증빙 사진을 지금 전송해 주세요."
        else:
            try:
                drive_url = image_url # 카카오 사진 원본 링크를 직접 사용 (구글 드라이브 업로드 생략)
                
                # '대기', 승인여부 'N' (기존 기록이 있다면 덮어쓰기)
                log_row = [target_date, nickname, "특휴", "대기", "N", "-", "-", "0", drive_url]
                sheets_client.upsert_daily_log(log_row)
                reply_text = "🏥 특휴 증빙 사진이 정상 접수되었습니다. 방장 확인(승인) 전까지는 대기 상태가 유지됩니다."
            except Exception as e:
                reply_text = f"이미지 업로드 중 에러 발생: {e}"

    else:
        # [일반 인증 / 반휴 인증] (이미지가 들어왔거나 요청하는 경우)
        if not image_url:
            if is_half_off:
                # 반휴 누르고 아직 사진 안 보냈으므로 상태 기억!
                user_states[userkey] = {"type": "반휴", "expires": now + timedelta(minutes=10)}
                reply_text = "🌗 반휴 적용을 위해 오늘 최소 1시간을 달성한 구루미 타이머 사진을 전송해 주세요. (이제 текст 없이 사진만 보내도 됩니다!)"
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
                drive_url = image_url # 카카오 사진 원본 링크 직접 사용 (구글 드라이브 업로드 생략)
                
                # OCR 서비스가 (종료시각, 당일공부시간(분), 누적시간(분), 원문) 튜플 반환
                ocr_result = ocr_service.extract_time_from_image(local_path)
                os.remove(local_path)
                
                end_time, duration, total_mnts, full_text = ocr_result[0], ocr_result[1], ocr_result[2], ocr_result[3]
                
                if not end_time or duration == 0:
                    reply_text = "❌ OCR 엔진이 시간 정보를 찾지 못했습니다. 숫자가 선명하게 나오도록 자르지 말고 다시 찍어주세요!"
                else:
                    bt_str = str(member_record.get("목표시간", "120")).strip()
                    import re
                    nums = re.findall(r'\d+', bt_str)
                    if not nums:
                        base_target = 120
                    else:
                        base_target = int(nums[0]) * 60 if "시간" in bt_str else int(nums[0])
                    final_target = target_override * 60 if target_override else base_target
                    
                    # --- [신규 로직] 시간 위조 및 지각 검증 ---
                    is_fake_date, is_absent, is_ontime = check_in_engine.validate_ocr_time(
                        target_date, end_time, duration, final_target
                    )
                    
                    # --- [신규 로직] 누적 시간(Total Time) 60분 오차 및 닉네임 교차 검증 ---
                    is_fake_time = False
                    is_fake_nickname = False
                    
                    # 1. 닉네임 일치 검사
                    # 닉네임에서 공백을 제거한 기준으로도 검색 (오인식 대비)
                    clean_nick = nickname.replace(" ", "")
                    # OCR 텍스트 내에서 본인 닉네임이 발견 안 되면 타인 사진 도용으로 간주
                    if clean_nick not in full_text.replace(" ", ""):
                        is_fake_nickname = True
                        
                    old_acc_str = str(member_record.get("최종누적", "0")).replace(",", "")
                    old_acc = int(old_acc_str) if old_acc_str.isdigit() else 0
                    
                    # 2. 누적시간 오차 검사
                    if total_mnts > 0:
                        diff = abs((old_acc + duration) - total_mnts)
                        if diff > 60:
                            is_fake_time = True
                            
                    # 벌금 계산
                    penalty = settlement_engine.calculate_penalty(
                        target_minutes=final_target, 
                        auth_minutes=duration, 
                        is_late_submit=not is_ontime, 
                        is_fake_time=is_fake_time or is_fake_nickname, # 닉네임 불일치도 조작(-5000)으로 동일 처리
                        is_fake_date=is_fake_date,
                        is_absent=is_absent
                    )
                    
                    if is_absent:
                        status_msg = "결석(목표미달)"
                    elif is_fake_date:
                        status_msg = "허위(예전사진)" 
                    elif is_fake_nickname:
                        status_msg = "조작(타인사진)"
                    elif is_fake_time:
                        status_msg = "조작(누적오류)"
                    else:
                        status_msg = "PASS" if penalty == 0 else "경고/지각발송"
                        
                    # --- [신규 로직] 예치금 즉시 차감 ---
                    if penalty < 0:
                        old_deposit_str = str(member_record.get("예치금", "0")).replace(",", "")
                        old_deposit = int(old_deposit_str) if old_deposit_str.replace("-", "").isdigit() else 0
                        new_deposit = old_deposit + penalty # penalty는 -500 등 음수값
                        sheets_client.update_cell("Member_Master", row_idx, 8, str(new_deposit)) # H열(8)이 예치금
                    
                    # 당일시간(종류), 사진누적(분->시간)
                    dur_str = f"{duration//60}시간 {duration%60}분"
                    tot_str = f"{total_mnts//60}시간 {total_mnts%60}분"
                    
                    # DB 로그 (Daily_Log: 날짜, 닉네임, 종류, 판정, 승인여부, 당일시간, 사진누적, 벌금액, 증빙) - Override 처리
                    log_row = [target_date, nickname, auth_type, status_msg, "-", dur_str, tot_str, str(penalty), drive_url]
                    sheets_client.upsert_daily_log(log_row)
                    
                    # 누적 시간 갱신 (정상 PASS일 때만, 혹은 fake가 아닐 때. 5는 E열)
                    if not is_fake_date and not is_fake_time and not is_fake_nickname and total_mnts > 0:
                        sheets_client.update_cell("Member_Master", row_idx, 5, str(total_mnts)) 
                    
                    reply_text = (
                        f"✅ [{auth_type}] 인증 제출이 완료되었습니다!\n\n"
                        f"- 판정결과: {status_msg}\n"
                        f"- 적용 목표시간: {final_target//60}시간 {final_target%60}분\n"
                        f"- 당일 공부시간: {dur_str}\n"
                        f"- 누적 공부시간: {tot_str}\n"
                        f"- 사진 인식시점: {end_time}\n"
                        f"- 감지된 닉네임 매칭: {'실패 (타인사진의심)' if is_fake_nickname else '성공'}\n"
                        f"- 이번 인증 벌금: {penalty}원"
                    )

            except Exception as e:
                reply_text = f"서버 처리 중 오류가 발생했습니다: {e}"

    return build_kakao_response(reply_text)
