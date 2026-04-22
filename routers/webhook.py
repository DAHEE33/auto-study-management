from fastapi import APIRouter, Request, BackgroundTasks
from pydantic import BaseModel
from typing import Dict, Any
from datetime import datetime, timedelta
import httpx
import tempfile
import os
import json
import re

from integrations.google_sheets import sheets_client
from integrations.google_drive import drive_client
from services.ocr_service import ocr_service
from services.settlement_engine import settlement_engine
from services.check_in_engine import check_in_engine

def parse_duration_to_min(dur_str: str) -> int:
    dur_str = str(dur_str).strip().replace(",", "")
    if not dur_str or dur_str == "-" or dur_str == "0":
        return 0
    m_h = re.search(r'(\d+)\s*시간', dur_str)
    m_m = re.search(r'(\d+)\s*분', dur_str)
    h = int(m_h.group(1)) if m_h else 0
    m = int(m_m.group(1)) if m_m else 0
    res = h * 60 + m
    if res == 0:
        nums = re.findall(r'\d+', dur_str)
        if nums:
            res = int(nums[0])
    return res

def format_min_to_str(total_min: int) -> str:
    return f"{total_min // 60}시간 {total_min % 60}분"

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

def update_sheets_in_background(row_idx: int, col_updates: list, log_row: list):
    """구글 시트 업데이트를 백그라운드에서 실행하여 카카오 응답 지연(5초 타임아웃) 방지"""
    try:
        from integrations.google_sheets import sheets_client
        for col_idx, val in col_updates:
            sheets_client.update_cell("Member_Master", row_idx, col_idx, val)
        sheets_client.upsert_daily_log(log_row)
        print("✅ [백그라운드] 구글 시트 업데이트 완료!")
    except Exception as e:
        print(f"❌ [백그라운드] 구글 시트 업데이트 중 에러 발생: {e}")

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

    # 2. 파라미터 또는 발화에서 이미지 URL 파싱 (미등록 유저 사진 유무를 빨리 알기 위해 위로 올림)
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

    member_record = sheets_client.get_member_by_userkey(userkey)
    
    if not member_record:
        # [자동 회원가입 로직]
        # 이미지를 보냈거나, 텍스트가 너무 길거나(15자), 하단 퀵리플라이 버튼을 누른 경우 가입 안내 문구 발송
        is_button_click = utterance in ["인증", "반휴 인증", "주휴 사용", "특휴 증빙하기", "내 현황", "목표 변경"]
        
        if image_url or len(utterance) > 15 or is_button_click:
            return build_kakao_response(
                "✨ 환영합니다! 평일 저녁 인증 스터디 봇입니다.\n"
                "구루미 닉네임 = 오픈채팅방 닉네임과 동일하게 등록합니다.\n\n"
                "사용하실 닉네임만 채팅창에 짧게 입력해 주세요!\n"
                "(예: 키뮤)"
            )
        
        # 그 외의 짧은 텍스트는 닉네임으로 간주하여 즉시 등록
        target_nick = utterance.strip()
        new_row = [target_nick, userkey, "활동", "2시간 0분", "0시간 0분", "1.0", "1", "10000", "-"]
        sheets_client.append_row("Member_Master", new_row)
        
        return build_kakao_response(
            f"✅ '{target_nick}'님, 가입이 완료되었습니다!\n"
            f"(기본 혜택: 주휴 1회, 월휴 1회)\n\n"

            f"하단의 메뉴 버튼을 이용해 인증을 시작해 보세요."
        )
        
    nickname = member_record.get("닉네임", "Unkown")
    row_idx = member_record.get("_row_index", -1)

    # [신규 기능 2: 목표 시간 변경] "목표변경 3시간" 또는 버튼 클릭
    utterance_clean = utterance.replace(" ", "")
    if utterance_clean.startswith("목표변경") or utterance_clean.startswith("목표시간") or utterance_clean.startswith("목표설정") or utterance == "목표 변경":
        nums = re.findall(r'\d+', utterance)
        if not nums:
            return build_kakao_response("🎯 목표시간 설정을 원하시나요?\n\n채팅창에 변경하실 시간과 함께 아래 양식으로 입력해 주세요!\n\n(예시)\n👉 목표변경 2시간 30분\n👉 목표시간 120\n👉 목표변경 3시간")
            
        new_target_minutes = parse_duration_to_min(utterance)
        if new_target_minutes < 120:
            return build_kakao_response("❌ 목표시간은 최소 2시간(120분) 이상부터 입력 가능합니다.")
            
        # 목표시간은 D열(4)
        sheets_client.update_cell("Member_Master", row_idx, 4, format_min_to_str(new_target_minutes))
        return build_kakao_response(f"✅ 목표 시간이 '{format_min_to_str(new_target_minutes)}'으로 변경 적용되었습니다!")

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
        return build_kakao_response("❌ 처리 기간이 지났습니다.\n(제출 마감: 익일 02:00, 인증 오픈: 당일 17:00)")

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
        # [현황 조회 - 대시보드 링크 제공]
        import urllib.parse
        encoded_nick = urllib.parse.quote(nickname)
        
        # request.base_url은 접속된 도메인(예: http://oracle-ip/)을 자동으로 반환합니다.
        # ngrok이나 포워딩이 있으면 스키마가 다를 수 있지만 기본적으로 동작
        dashboard_url = f"{request.base_url}dashboard?user={encoded_nick}"
        
        reply_text = (
            f"✨ [{nickname}]님을 위한 전용 대시보드가 준비되었습니다!\n\n"
            f"👇 아래 링크(개인 전용)를 눌러 실시간 스터디 순위와 잔디심기 현황을 가장 예쁜 화면으로 확인하세요!\n\n"
            f"🔗 {dashboard_url}"
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
            
            # --- [신규 로직] 만약 오늘 이미 결석/지각 등으로 벌금이 차감되어 있었다면 환불 ---
            old_penalty = sheets_client.get_daily_penalty(target_date, nickname)
            if old_penalty < 0:
                old_deposit_str = str(member_record.get("예치금", "0")).replace(",", "")
                old_deposit = int(old_deposit_str) if old_deposit_str.replace("-", "").isdigit() else 0
                new_deposit = old_deposit + abs(old_penalty) # 차감된 벌금만큼 100% 다시 더해줌
                sheets_client.update_cell("Member_Master", row_idx, 8, str(new_deposit)) # H열(8)
                msg += f"\n(기존 무효 처리: 부과되었던 패널티 {old_penalty}원이 예치금으로 반환되었습니다.)"
            
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
                reply_text = "🌗 반휴 적용을 위해 오늘 최소 1시간을 달성한 구루미 타이머 사진을 전송해 주세요."
            else:
                reply_text = "🔥 타이머와 누적시간이 잘 보이는 [구루미 메인 화면] 캡처 사진을 전송해 주셔야 공부 판독이 가능합니다."
        else:
            auth_type = "반휴" if is_half_off else "일반"
            target_override = None
            pending_deduct_amt = 0
            
            # 반휴일 경우 우선 잔여휴무 검증
            if auth_type == "반휴":
                is_approved, msg, deduct_amt = check_in_engine.process_leave_request(member_record, "반휴")
                if not is_approved:
                    return build_kakao_response(msg)
                
                target_override = 1 # 반휴는 목표 1시간으로 고정
                pending_deduct_amt = deduct_amt # 검증 통과 시 차감하기 위해 보류

            # 타임아웃 방지: OCR만 동기처리, 구글시트 업데이트는 백그라운드로 미룸
            try:
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
                    base_target = parse_duration_to_min(bt_str)
                    if base_target == 0:
                        base_target = 120
                    final_target = target_override * 60 if target_override else base_target
                    
                    # --- [신규 로직] 시간 위조 및 지각 검증 ---
                    is_fake_date, is_absent, is_ontime = check_in_engine.validate_ocr_time(
                        target_date, end_time, duration, final_target
                    )
                    
                    # --- [신규 로직] 누적 시간(Total Time) 60분 오차 및 닉네임 교차 검증 ---
                    is_fake_time = False
                    is_fake_nickname = False
                    
                    # 1. 닉네임 일치 검사 (단순 로깅용으로만 사용, 오인식으로 인한 억울한 패널티 방지)
                    clean_nick = nickname.replace(" ", "")
                    if clean_nick not in full_text.replace(" ", ""):
                        print(f"⚠️ [주의] 닉네임 불일치 감지: DB={clean_nick}, OCR텍스트에 없음")
                        # is_fake_nickname = True # 오인식으로 인한 -5000원 방지를 위해 비활성화
                        
                    old_acc_str = str(member_record.get("최종누적", "0"))
                    old_acc = parse_duration_to_min(old_acc_str)
                    
                    # 2. 누적시간 오차 검사
                    # [재인증 보정 로직] 이미 오늘 인증을 해서 Member_Master의 '최종누적'이 오늘치(duration)를 포함해버린 경우, 
                    # 다시 인증하면 (old_acc + duration)이 실제 total_mnts보다 duration만큼 커져서 조작으로 오탐지됩니다.
                    # 이를 방지하기 위해 오늘 이미 인증한 내역이 있는지 확인하여 old_acc에서 이전 인증 시간을 빼줍니다.
                    today_auth = sheets_client.get_today_auth_history(target_date, nickname)
                    if today_auth:
                        prev_duration = today_auth.get("prev_duration", 0)
                        prev_status = today_auth.get("prev_status", "")
                        # 이전 기록이 허위/조작이 아니었다면 (즉, 최종누적이 업데이트된 기록이라면) 그만큼 빼서 "오늘 첫 인증 직전의 누적"으로 되돌림
                        if prev_duration > 0 and "허위" not in prev_status and "조작" not in prev_status:
                            old_acc = max(0, old_acc - prev_duration)
                            print(f"🔄 [재인증 보정] {nickname}님의 오늘 이전 인증시간({prev_duration}분)을 제외하고 검증합니다. 보정된 기준누적: {old_acc}")

                    if total_mnts > 0 and old_acc > 0:
                        diff = abs((old_acc + duration) - total_mnts)
                        if diff > 60:
                            is_fake_time = True
                            print(f"🚨 [조작 감지] 닉네임: {nickname}, 계산된오차: {diff}분 (DB기준누적:{old_acc} + 당일:{duration} != OCR총누적:{total_mnts})")
                            
                    # 벌금 계산
                    penalty = settlement_engine.calculate_penalty(
                        target_minutes=final_target, 
                        auth_minutes=duration, 
                        is_late_submit=not is_ontime, 
                        is_fake_time=is_fake_time,
                        is_fake_date=is_fake_date,
                        is_absent=is_absent
                    )
                    
                    # 미달 판단: 1시 이후 전송실패(is_absent)이거나 인증 시간이 목표시간(반휴 1시간)에 못 미칠 때
                    is_failed = is_absent or (duration < final_target)

                    if is_failed:
                        status_msg = "결석(목표미달)"
                    elif is_fake_date:
                        status_msg = "허위(예전사진)" 
                    elif is_fake_time:
                        status_msg = "조작(누적오류)"
                    else:
                        status_msg = "PASS" if penalty == 0 else "경고/지각발송"
                        
                    # --- [백그라운드 처리용 변수 수집] 구글 API 호출(약 3초)을 뒤로 미뤄서 타임아웃 회피 ---
                    col_updates = []
                    
                    if auth_type == "반휴" and not is_failed and not is_fake_date and not is_fake_time:
                        col_idx = 6 # 주간휴무 컬럼
                        new_val = max(0.0, float(member_record.get("주간휴무", "0")) - pending_deduct_amt)
                        col_updates.append((col_idx, str(new_val)))
                        
                    if penalty < 0:
                        old_deposit_str = str(member_record.get("예치금", "0")).replace(",", "")
                        old_deposit = int(old_deposit_str) if old_deposit_str.replace("-", "").isdigit() else 0
                        new_deposit = old_deposit + penalty # penalty는 -500 등 음수값
                        col_updates.append((8, str(new_deposit))) # H열(8)이 예치금
                    
                    # 당일시간(종류), 사진누적(분->시간)
                    dur_str = f"{duration//60}시간 {duration%60}분"
                    tot_str = f"{total_mnts//60}시간 {total_mnts%60}분"
                    log_row = [target_date, nickname, auth_type, status_msg, "-", dur_str, tot_str, str(penalty), drive_url]
                    
                    if not is_fake_date and not is_fake_time and total_mnts > 0:
                        col_updates.append((5, format_min_to_str(total_mnts)))
                        
                    # 🚀 백그라운드로 구글 시트 업데이트 넘김 (응답 시간 대폭 단축!)
                    background_tasks.add_task(update_sheets_in_background, row_idx, col_updates, log_row)
                    
                    reply_text = (
                        f"✅ [{auth_type}] 인증 제출이 완료되었습니다!\n\n"
                        f"- 판정결과: {status_msg}\n"
                        f"- 적용 목표시간: {final_target//60}시간 {final_target%60}분\n"
                        f"- 당일 공부시간: {dur_str}\n"
                        f"- 누적 공부시간: {tot_str}\n"
                        f"- 사진 인식시점: {end_time}\n"
                        f"- 이번 인증 벌금: {penalty}원"
                    )

            except Exception as e:
                reply_text = f"서버 처리 중 오류가 발생했습니다: {e}"
                print(f"⚠️ Exception 됨: {e}")

    print(f"📨 [카카오 응답 전송]: {reply_text[:100]}...")
    return build_kakao_response(reply_text)
