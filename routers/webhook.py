from fastapi import APIRouter, Request, BackgroundTasks
from pydantic import BaseModel
from typing import Dict, Any
from datetime import datetime, timedelta
import httpx
import tempfile
import os
import json
import re
import traceback
import uuid
import threading

from integrations.google_sheets import sheets_client, SheetReadError
from integrations.google_drive import drive_client
from services.ocr_service import ocr_service
from services.settlement_engine import settlement_engine
from services.check_in_engine import check_in_engine
from services.leave_reset_service import leave_reset_service

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

def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default

def get_monthly_leave_cap(target_date: str) -> float:
    """
    해당 target_date(YYYY-MM-DD)가 속한 월의 월휴 최대치를 반환합니다.
    Admin_Config에 값이 없으면 기본 1.0을 사용합니다.
    """
    month_key = str(target_date).strip()[:7]
    if len(month_key) != 7:
        return 1.0

    admin_rows = sheets_client.get_sheet_records("Admin_Config")
    candidates = []
    for row in admin_rows:
        row_date = str(row.get("날짜", "")).strip()
        row_event = str(row.get("이벤트 타입", "")).strip()
        if row_date == month_key and row_event in {"월휴개수", "특휴개수"}:
            candidates.append(row)

    if not candidates:
        return 1.0

    latest = candidates[-1]
    for key in ("월별월휴개수", "월별특휴개수", "목표시간 조정"):
        raw = str(latest.get(key, "")).strip()
        if not raw or raw == "-":
            continue
        try:
            return max(0.0, float(int(float(raw))))
        except ValueError:
            continue
    return 1.0

def build_daily_log_row(
    target_date: str,
    nickname: str,
    auth_type: str,
    status_msg: str,
    approval: str,
    daily_time: str,
    total_time: str,
    penalty: int,
    image_id: str,
    weekly_deduct: float = 0.0,
    monthly_deduct: float = 0.0,
) -> list:
    return [
        target_date,
        nickname,
        auth_type,
        status_msg,
        approval,
        daily_time,
        total_time,
        str(penalty),
        image_id,
        f"{weekly_deduct:.1f}",
        f"{monthly_deduct:.1f}",
    ]

router = APIRouter(prefix="/webhook", tags=["Webhook"])

# 봇이 사용자 요청 맥락을 기억하기 위한 상태 저장소 (메모리 방식)
# 형태: { "UserKey": {"type": "반휴" | "특휴", "expires": datetime_object} }
user_states = {}
photo_auth_locks = {}
photo_auth_locks_guard = threading.Lock()
RESERVED_NICK_INPUTS = {"인증", "반휴 인증", "주휴 사용", "월휴 사용", "특휴 증빙하기", "내 현황", "목표 변경"}
BUTTON_FALLBACK_HINT = "\n\n(버튼이 안 보이면 채팅창에 '인증' 또는 '반휴 인증'을 직접 입력해 주세요.)"

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
                {"messageText": "월휴 사용", "action": "message", "label": "🌙 월휴 사용"},
                {"messageText": "특휴 증빙하기", "action": "message", "label": "🏥 특휴 신청"},
                {"messageText": "내 현황", "action": "message", "label": "📈 내 현황 확인"},
                {"messageText": "목표 변경", "action": "message", "label": "🎯 목표시간 변경"}
            ]
        }
    }


def build_kakao_callback_wait_response() -> Dict[str, Any]:
    """콜백 대기 응답에는 일반 template을 섞지 않습니다."""
    return {"version": "2.0", "useCallback": True}

async def download_image(url: str) -> str:
    """URL에서 이미지를 임시 파일로 다운로드 후 경로 반환"""
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        resp.raise_for_status()
        
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
    temp_file.write(resp.content)
    temp_file.close()
    return temp_file.name


def extract_image_url(params: Dict[str, Any], user_request: Dict[str, Any], utterance: str) -> str:
    """카카오 payload의 다양한 케이스에서 이미지 URL을 최대한 안정적으로 추출합니다."""
    candidates = []

    def _walk(obj: Any):
        if isinstance(obj, dict):
            for key, val in obj.items():
                if key in {"origin", "value", "resolvedValue", "url", "imageUrl"} and isinstance(val, str):
                    candidates.append(val)
                _walk(val)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    _walk(params)
    _walk(user_request.get("params", {}))
    if utterance:
        candidates.append(utterance)

    for raw in candidates:
        if "http" not in raw:
            continue
        cleaned = raw.strip()
        if cleaned.startswith("List(") and cleaned.endswith(")"):
            cleaned = cleaned[5:-1]

        # payload 문자열 안에 URL이 섞여 있어도 추출할 수 있도록 정규식 보강
        urls = re.findall(r"https?://[^\s,\)]+", cleaned)
        for url in urls:
            if "http" in url:
                return url

        if cleaned.startswith("http"):
            return cleaned

    return ""

def get_nickname_validation_error(raw_nickname: str) -> str:
    nickname = raw_nickname.strip()
    if not nickname:
        return "닉네임이 비어있습니다."
    if nickname in RESERVED_NICK_INPUTS:
        return "메뉴 버튼 문구는 닉네임으로 사용할 수 없습니다."
    if nickname.startswith("http"):
        return "링크는 닉네임으로 사용할 수 없습니다."
    if len(nickname) > 15:
        return "닉네임은 15자 이하로 입력해 주세요."
    return ""

def is_duplicate_nickname(userkey: str, nickname: str) -> bool:
    records = sheets_client.get_sheet_records("Member_Master")
    for row in records:
        row_userkey = str(row.get("UserKey", "")).strip()
        row_nickname = str(row.get("닉네임", "")).strip()
        if row_userkey != userkey and row_nickname == nickname:
            return True
    return False


def is_optional_participation_day(events: list, target_date: str) -> bool:
    return any(
        str(event.get("날짜", "")).strip() == target_date
        and "자율참여" in str(event.get("이벤트 타입", ""))
        for event in events
    )


def activate_member_if_needed(row_idx: int, member_record: dict, source: str = "unknown") -> None:
    """
    신규 가입자를 '대기'로 두고, 첫 인증/휴무 사용 시점에 '활동'으로 전환합니다.
    """
    current_status = str(member_record.get("상태", "")).strip()
    if current_status == "활동":
        return

    # 예치금 소진자는 자동 복귀시키지 않습니다. (관리자 수동 복귀)
    if current_status == "예치금 소진":
        return

    # 자동 전환은 신규 가입 대기 상태에서만 허용합니다.
    if current_status != "대기":
        return

    ok = sheets_client.update_cell("Member_Master", row_idx, 3, "활동")
    if ok:
        member_record["상태"] = "활동"
        print(f"✅ [{source}] 멤버 상태 전환: row={row_idx}, {current_status} -> 활동")
    else:
        print(f"❌ [{source}] 멤버 상태 전환 실패: row={row_idx}, from={current_status}")

def update_sheets_in_background(request_id: str, row_idx: int, col_updates: list, log_row: list):
    """구글 시트 업데이트를 백그라운드에서 실행하여 카카오 응답 지연(5초 타임아웃) 방지"""
    try:
        from integrations.google_sheets import sheets_client
        print(f"[{request_id}] 🧾 [백그라운드] 시트 업데이트 시작 row={row_idx}, updates={len(col_updates)}")
        for col_idx, val in col_updates:
            ok = sheets_client.update_cell("Member_Master", row_idx, col_idx, val)
            if not ok:
                print(f"[{request_id}] ❌ [백그라운드] update_cell 실패 row={row_idx}, col={col_idx}, val={val}")

        log_ok = sheets_client.upsert_daily_log(log_row)
        if not log_ok:
            print(f"[{request_id}] ❌ [백그라운드] upsert_daily_log 실패: {log_row}")
        else:
            print(f"[{request_id}] ✅ [백그라운드] 구글 시트 업데이트 완료")
    except Exception as e:
        print(f"[{request_id}] ❌ [백그라운드] 구글 시트 업데이트 중 에러 발생: {e}")
        print(traceback.format_exc())

def _photo_auth_lock(nickname: str, target_date: str):
    key = (nickname, target_date)
    with photo_auth_locks_guard:
        return photo_auth_locks.setdefault(key, threading.Lock())


def _history_row(target_date, nickname, auth_type, status, daily, total, penalty, image_url, submitted_at, reason):
    return [
        target_date, nickname, auth_type, status, "-",
        format_min_to_str(daily) if daily is not None else "-",
        format_min_to_str(total) if total is not None else "-",
        str(penalty), image_url, submitted_at.strftime("%Y-%m-%d %H:%M:%S"), reason,
    ]


def save_latest_photo_failure(sheet_client, target_date, nickname, auth_type, status, daily, total, image_url):
    """최신 실패를 표시하되 이미 실제로 차감된 금액/휴무는 환불 없이 보존합니다."""
    sheet_client.clear_cache("Daily_Log")
    previous = get_existing_daily_log_row(target_date, nickname, sheet_client)
    penalty = int(previous[7]) if previous else 0
    weekly = safe_float(previous[9]) if previous else 0.0
    monthly = safe_float(previous[10]) if previous else 0.0
    if previous and previous[3] == "PASS" and weekly == monthly == 0:
        weekly = {"주휴": 1.0, "반휴": 0.5}.get(previous[2], 0.0)
        monthly = 1.0 if previous[2] == "월휴" else 0.0
    return sheet_client.upsert_daily_log(build_daily_log_row(
        target_date, nickname, auth_type, status, "-",
        format_min_to_str(daily) if daily is not None else "-",
        format_min_to_str(total) if total is not None else "-",
        penalty, image_url, weekly, monthly,
    ))


def _send_kakao_callback(callback_url: str, message: str, request_id: str) -> bool:
    if not callback_url:
        return True
    try:
        response = httpx.post(callback_url, json=build_kakao_response(message), timeout=10.0)
        response.raise_for_status()
        body = response.json()
        if body.get("status") != "SUCCESS":
            print(f"[{request_id}] ❌ 카카오 콜백 실패: status={body.get('status', 'unknown')}")
            return False
        print(f"[{request_id}] ✅ 카카오 콜백 전송 완료")
        return True
    except Exception as exc:
        print(f"[{request_id}] ❌ 카카오 콜백 전송 오류: {type(exc).__name__}")
        return False


def process_photo_auth_in_background(
    request_id: str, image_url: str, auth_type: str, nickname: str,
    member_record: dict, row_idx: int, target_date: str,
    target_override, pending_deduct_amt: float, now: datetime, callback_url: str = ""
):
    """
    [카카오 5초 타임아웃 완전 회피]
    사진 다운로드 → OCR → 벌금 계산 → 구글 시트 기록을 모두 백그라운드에서 처리합니다.
    카카오에게는 즉시 '접수 완료' 응답을 보낸 뒤, 이 함수가 뒤에서 천천히 돌아갑니다.
    """
    final_message = "인증 처리에 실패했습니다. 잠시 후 다시 시도해 주세요."
    local_path = None
    history_written = False
    try:
        from integrations.google_sheets import sheets_client as bg_sheets
        from services.ocr_service import ocr_service as bg_ocr
        from services.settlement_engine import settlement_engine as bg_engine
        from services.check_in_engine import check_in_engine as bg_checkin

        print(f"[{request_id}] 🔄 [백그라운드-사진인증] 처리 시작: {nickname} ({auth_type})")

        # 기존 규칙: 대기 회원은 첫 사진 인증 시도 시 활동 상태로 전환합니다.
        if str(member_record.get("상태", "")).strip() == "대기":
            with _photo_auth_lock(nickname, target_date):
                if bg_sheets.update_cell("Member_Master", row_idx, 3, "활동"):
                    member_record["상태"] = "활동"

        # 1. 이미지 다운로드 (동기 방식으로 변환)
        resp = httpx.get(image_url, timeout=15)
        resp.raise_for_status()
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
        temp_file.write(resp.content)
        temp_file.close()
        local_path = temp_file.name
        drive_url = image_url

        # 2. OCR 파싱
        ocr_result = bg_ocr.extract_time_from_image(local_path, nickname)
        if not ocr_result.succeeded:
            reason = ocr_result.error or "출석표를 판독하지 못했습니다."
            history_ok = bg_sheets.append_row("Photo_Auth_History", _history_row(
                target_date, nickname, auth_type, "판독실패", None, None, 0, drive_url, now, reason
            ))
            history_written = history_ok
            with _photo_auth_lock(nickname, target_date):
                log_ok = save_latest_photo_failure(bg_sheets, target_date, nickname, auth_type,
                                                   "판독실패", None, None, drive_url)
            final_message = f"인증 거절: {reason}\n최신 제출 결과에 판독 실패로 기록했습니다."
            if not history_ok or not log_ok:
                final_message = "처리 실패: 제출 이력을 저장하지 못했습니다. 인증은 확정되지 않았습니다."
            return final_message

        end_time = ocr_result.attendance_at
        duration = ocr_result.daily_minutes
        total_mnts = ocr_result.total_minutes

        # 3. 목표시간 계산
        bt_str = str(member_record.get("목표시간", "120")).strip()
        base_target = parse_duration_to_min(bt_str)
        if base_target == 0:
            base_target = 120
        final_target = target_override * 60 if target_override else base_target

        # 4. 날짜 형식/과거 날짜/허용 시각을 서로 구분합니다.
        time_validation = bg_checkin.validate_ocr_attendance(
            target_date, end_time, duration, final_target
        )
        with _photo_auth_lock(nickname, target_date):
            bg_sheets.clear_cache("Member_Master")
            bg_sheets.clear_cache("Daily_Log")
            latest_member = bg_sheets.get_member_by_userkey(str(member_record.get("UserKey", "")))
            if not latest_member:
                raise RuntimeError("회원 최신 정보를 찾지 못했습니다.")
            row_idx = latest_member.get("_row_index", row_idx)
            existing = bg_sheets.get_today_auth_history(target_date, nickname)

            # 날짜 검증 실패도 최신 제출 결과에 표시합니다.
            if not time_validation.valid:
                penalty = -5000 if time_validation.is_past_date and not existing else 0
                status_msg = "과거사진" if time_validation.is_past_date else "검증거절"
                reason = time_validation.reason
                if existing:
                    reason += " 최신 제출 결과를 갱신하며 기존 차감 내역은 유지합니다."
                elif time_validation.is_past_date:
                    old_deposit = int(str(latest_member.get("예치금", "0")).replace(",", "") or 0)
                    deposit_ok = bg_sheets.update_cell("Member_Master", row_idx, 8, str(old_deposit + penalty))
                    log_ok = bg_sheets.upsert_daily_log([
                        target_date, nickname, auth_type, status_msg, "-", format_min_to_str(duration),
                        format_min_to_str(total_mnts), str(penalty), drive_url,
                    ])
                    if not deposit_ok or not log_ok:
                        final_message = "처리 실패: 시트 저장에 실패하여 인증을 확정하지 못했습니다."
                        return final_message
                log_ok = save_latest_photo_failure(bg_sheets, target_date, nickname, auth_type,
                                                   status_msg, duration, total_mnts, drive_url)
                history_ok = bg_sheets.append_row("Photo_Auth_History", _history_row(
                    target_date, nickname, auth_type, status_msg, duration, total_mnts,
                    penalty, drive_url, now, reason,
                ))
                history_written = history_ok
                if not history_ok or not log_ok:
                    final_message = "처리 실패: 제출 결과를 모두 저장하지 못했습니다."
                elif time_validation.is_past_date:
                    final_message = f"과거 날짜 사진: 사진 {end_time[:10]} / 인증 대상일 {target_date} / {penalty:,}원"
                    if existing:
                        final_message += "\n최신 제출 결과로 기록했으며 기존 차감 내역은 유지됩니다."
                else:
                    final_message = f"인증 거절: {reason}"
                return final_message

            previous_total = parse_duration_to_min(latest_member.get("최종누적", "0"))
            if total_mnts <= previous_total:
                relation = "같습니다" if total_mnts == previous_total else "감소했습니다"
                reason = f"사진 누적시간이 기존 최종누적보다 {relation}. 최신 제출 결과에 누적시간 검증 실패로 기록했습니다."
                history_ok = bg_sheets.append_row("Photo_Auth_History", _history_row(
                    target_date, nickname, auth_type, "누적거절", duration, total_mnts, 0,
                    drive_url, now, reason,
                ))
                history_written = history_ok
                log_ok = save_latest_photo_failure(bg_sheets, target_date, nickname, auth_type,
                                                   "누적거절", duration, total_mnts, drive_url)
                final_message = f"인증 거절: {reason}"
                if not history_ok or not log_ok:
                    final_message = "처리 실패: 제출 이력을 저장하지 못했습니다. 인증은 확정되지 않았습니다."
                return final_message

            # 판독과 검증이 모두 끝난 뒤에만 기존 정상 결과의 환불/전환을 수행합니다.
            previous_log_row = get_existing_daily_log_row(target_date, nickname, bg_sheets)
            refund_updates, refund_msg, refunded_record = build_refund_member_updates(
                target_date, nickname, latest_member, bg_sheets
            )
            is_absent = time_validation.is_absent_due_to_late
            penalty = bg_engine.calculate_penalty(
                final_target, duration, not time_validation.is_ontime, False, False, is_absent
            )
            is_failed = is_absent or duration < final_target
            status_msg = "결석(목표미달)" if is_failed else "PASS"
            col_updates = list(refund_updates)
            col_updates.append((5, "최종누적", format_min_to_str(total_mnts)))
            applied_weekly_deduct = 0.0
            if auth_type == "반휴" and not is_failed:
                applied_weekly_deduct = pending_deduct_amt
                new_leave = max(0.0, float(refunded_record.get("주간휴무", "0")) - applied_weekly_deduct)
                col_updates.append((6, "주간휴무", str(new_leave)))
            if penalty < 0:
                old_deposit = int(str(refunded_record.get("예치금", "0")).replace(",", "") or 0)
                new_deposit = old_deposit + penalty
                col_updates.append((8, "예치금", str(new_deposit)))
                if new_deposit <= 0:
                    col_updates.extend([(3, "상태", "예치금 소진"), (13, "탈퇴일", now.strftime("%Y-%m-%d"))])

            log_row = build_daily_log_row(
                target_date, nickname, auth_type, status_msg, "-", format_min_to_str(duration),
                format_min_to_str(total_mnts), penalty, drive_url, applied_weekly_deduct, 0.0,
            )
            log_ok = commit_daily_log_and_member_updates(
                bg_sheets, row_idx, latest_member, log_row, col_updates, previous_log_row
            )
            reason = "현재 최종 기록으로 적용했습니다." if log_ok else "최종 기록 저장에 실패했습니다."
            history_ok = bg_sheets.append_row("Photo_Auth_History", _history_row(
                target_date, nickname, auth_type, status_msg, duration, total_mnts,
                penalty, drive_url, now, reason,
            ))
            history_written = history_ok
            if not log_ok or not history_ok:
                final_message = "처리 실패: 시트 저장을 모두 완료하지 못해 인증 확정을 안내할 수 없습니다."
                return final_message

            if is_failed:
                final_message = (
                    f"시간 부족: 당일 {format_min_to_str(duration)} / 목표 {format_min_to_str(final_target)} / 벌금 {penalty:,}원"
                )
            else:
                final_message = (
                    f"인증 완료: 당일 {format_min_to_str(duration)} / 목표 {format_min_to_str(final_target)} / 적용 날짜 {target_date}"
                )
            if refund_msg:
                final_message += refund_msg
            print(f"[{request_id}] ✅ [백그라운드-사진인증] 완료: {nickname} → {status_msg}")
            return final_message

    except Exception as e:
        print(f"[{request_id}] ❌ [백그라운드-사진인증] 에러 발생: {e}")
        print(traceback.format_exc())
        if not history_written:
            try:
                history_written = sheets_client.append_row("Photo_Auth_History", _history_row(
                    target_date, nickname, auth_type, "처리실패", None, None, 0,
                    image_url, now, "사진 다운로드 또는 인증 처리 중 오류가 발생했습니다.",
                ))
                with _photo_auth_lock(nickname, target_date):
                    save_latest_photo_failure(bg_sheets, target_date, nickname, auth_type,
                                              "처리실패", None, None, image_url)
                if not history_written:
                    final_message = "처리 실패: 제출 이력도 저장하지 못했습니다. 인증은 확정되지 않았습니다."
            except Exception:
                final_message = "처리 실패: 제출 이력도 저장하지 못했습니다. 인증은 확정되지 않았습니다."
        return final_message
    finally:
        if local_path and os.path.exists(local_path):
            os.remove(local_path)
        _send_kakao_callback(callback_url, final_message, request_id)

def get_existing_daily_log_row(target_date: str, nickname: str, sheet_client=sheets_client) -> list:
    """기존 Daily_Log 행을 upsert_daily_log에 다시 넣을 수 있는 형태로 반환합니다."""
    records = sheet_client.get_sheet_records("Daily_Log")
    for row in records:
        if str(row.get("날짜", "")) == target_date and str(row.get("닉네임", "")) == nickname:
            return [
                str(row.get("날짜", "")),
                str(row.get("닉네임", "")),
                str(row.get("유형", "")),
                str(row.get("판정", "")),
                str(row.get("승인여부(특휴시)", "")),
                str(row.get("당일시간", "")),
                str(row.get("사진누적", "")),
                str(row.get("벌금액", "0")),
                str(row.get("이미지ID", "")),
                f"{safe_float(row.get('차감주휴', 0.0)):.1f}",
                f"{safe_float(row.get('차감월휴', 0.0)):.1f}",
            ]
    return []

def build_refund_member_updates(target_date: str, nickname: str, member_record: dict, sheet_client=sheets_client):
    """기존 Daily_Log를 새 기록으로 덮을 때 필요한 Member_Master 환불 업데이트를 계산만 합니다."""
    today_auth = sheet_client.get_today_auth_history(target_date, nickname)
    refund_msg = ""
    preview_record = dict(member_record)
    updates = []
    if not today_auth:
        return updates, refund_msg, preview_record

    prev_type = today_auth.get("prev_type", "")
    prev_weekly_deduct = safe_float(today_auth.get("prev_weekly_deduct", 0.0))
    prev_monthly_deduct = safe_float(today_auth.get("prev_monthly_deduct", 0.0))
    if prev_weekly_deduct == 0 and prev_monthly_deduct == 0 and today_auth.get("prev_status") == "PASS":
        if prev_type == "주휴":
            prev_weekly_deduct = 1.0
        elif prev_type == "반휴":
            prev_weekly_deduct = 0.5
        elif prev_type == "월휴":
            prev_monthly_deduct = 1.0

    if prev_weekly_deduct > 0:
        old_val = safe_float(preview_record.get("주간휴무", "0"))
        refund_amount = min(prev_weekly_deduct, max(0.0, 1.0 - old_val))
        if refund_amount > 0:
            new_val = old_val + refund_amount
            updates.append((6, "주간휴무", str(new_val)))
            preview_record["주간휴무"] = str(new_val)
            refund_msg += f"\n(이전 주간휴무 차감분 {refund_amount:.1f}이 환불되었습니다.)"

    if prev_monthly_deduct > 0:
        old_val = safe_float(preview_record.get("남은월휴", "0"))
        monthly_cap = get_monthly_leave_cap(target_date)
        refund_amount = min(prev_monthly_deduct, max(0.0, monthly_cap - old_val))
        if refund_amount > 0:
            new_val = old_val + refund_amount
            updates.append((7, "남은월휴", str(new_val)))
            preview_record["남은월휴"] = str(new_val)
            refund_msg += f"\n(이전 월휴 차감분 {refund_amount:.1f}이 환불되었습니다.)"

    if prev_type == "특휴":
        refund_msg += "\n(이전 특휴 신청 내역이 취소되었습니다.)"

    old_penalty = int(today_auth.get("prev_penalty", sheet_client.get_daily_penalty(target_date, nickname)))
    if old_penalty < 0:
        old_deposit_str = str(preview_record.get("예치금", "0")).replace(",", "")
        old_deposit = int(old_deposit_str) if old_deposit_str.replace("-", "").isdigit() else 0
        new_deposit = old_deposit + abs(old_penalty)
        updates.append((8, "예치금", str(new_deposit)))
        preview_record["예치금"] = str(new_deposit)
        refund_msg += f"\n(기존 패널티 {old_penalty}원이 예치금으로 반환되었습니다.)"

    return updates, refund_msg, preview_record

def rollback_member_updates(sheet_client, row_idx: int, member_record: dict, applied_updates: list):
    for col_idx, key, old_val in reversed(applied_updates):
        sheet_client.update_cell("Member_Master", row_idx, col_idx, old_val)
        if key:
            member_record[key] = old_val

def apply_member_updates(sheet_client, row_idx: int, member_record: dict, updates: list):
    applied_updates = []
    for col_idx, key, new_val in updates:
        old_val = str(member_record.get(key, "")) if key else ""
        if not sheet_client.update_cell("Member_Master", row_idx, col_idx, new_val):
            rollback_member_updates(sheet_client, row_idx, member_record, applied_updates)
            return False, []
        applied_updates.append((col_idx, key, old_val))
        if key:
            member_record[key] = str(new_val)
    return True, applied_updates

def commit_daily_log_and_member_updates(
    sheet_client, row_idx: int, member_record: dict, log_row: list, member_updates: list, previous_log_row: list
) -> bool:
    """Daily_Log와 Member_Master를 함께 반영하고 실패 시 가능한 범위에서 이전 상태로 되돌립니다."""
    if previous_log_row:
        if not sheet_client.upsert_daily_log(log_row):
            return False
        ok, _ = apply_member_updates(sheet_client, row_idx, member_record, member_updates)
        if not ok:
            sheet_client.upsert_daily_log(previous_log_row)
            return False
        return True

    ok, applied_updates = apply_member_updates(sheet_client, row_idx, member_record, member_updates)
    if not ok:
        return False
    if not sheet_client.upsert_daily_log(log_row):
        rollback_member_updates(sheet_client, row_idx, member_record, applied_updates)
        return False
    return True

def preview_member_record_after_refund(target_date: str, nickname: str, member_record: dict) -> dict:
    """이전 기록을 환불한다고 가정한 멤버 상태를 반환합니다. 시트에는 쓰지 않습니다."""
    from integrations.google_sheets import sheets_client
    today_auth = sheets_client.get_today_auth_history(target_date, nickname)
    preview_record = dict(member_record)
    if not today_auth:
        return preview_record

    prev_type = today_auth.get("prev_type", "")
    prev_weekly_deduct = safe_float(today_auth.get("prev_weekly_deduct", 0.0))
    prev_monthly_deduct = safe_float(today_auth.get("prev_monthly_deduct", 0.0))
    if prev_weekly_deduct == 0 and prev_monthly_deduct == 0 and today_auth.get("prev_status") == "PASS":
        if prev_type == "주휴":
            prev_weekly_deduct = 1.0
        elif prev_type == "반휴":
            prev_weekly_deduct = 0.5
        elif prev_type == "월휴":
            prev_monthly_deduct = 1.0

    if prev_weekly_deduct > 0:
        old_val = safe_float(preview_record.get("주간휴무", "0"))
        refund_amount = min(prev_weekly_deduct, max(0.0, 1.0 - old_val))
        if refund_amount > 0:
            preview_record["주간휴무"] = str(old_val + refund_amount)

    if prev_monthly_deduct > 0:
        old_val = safe_float(preview_record.get("남은월휴", "0"))
        monthly_cap = get_monthly_leave_cap(target_date)
        refund_amount = min(prev_monthly_deduct, max(0.0, monthly_cap - old_val))
        if refund_amount > 0:
            preview_record["남은월휴"] = str(old_val + refund_amount)

    return preview_record

@router.post("")
async def kakao_webhook(request: Request, background_tasks: BackgroundTasks):
    """카카오톡 채널 챗봇(오픈빌더)으로부터 들어오는 요청을 처리합니다."""
    body = await request.json()
    user_request = body.get("userRequest", {})
    callback_url = str(user_request.get("callbackUrl") or "").strip()
    utterance = user_request.get("utterance", "").strip()
    action = body.get("action", {})
    params = action.get("detailParams", {})
    request_id = uuid.uuid4().hex[:8]
    block = user_request.get("block") or {}
    print(f"[{request_id}] callback_present={bool(callback_url)}, block_id={block.get('id', '')}, block_name={block.get('name', '')}")
    
    # 1. UserKey 추출 및 멤버 확보
    userkey = user_request.get("user", {}).get("id", "")

    try:
        leave_reset_service.run_if_needed()
    except Exception as e:
        print(f"⚠️ 휴무 자동 갱신 체크 실패(webhook): {e}")
    
    # 📝 [로그 출력] 챗봇이 보낸 UserKey를 서버 터미널에서 즉시 확인합니다.
    print(f"\n================ [카카오 웹훅 수신:{request_id}] ================")
    print(f"► 유저키(UserKey): {userkey}")
    print(f"► 수신 텍스트(utterance): {utterance}")
    print(f"====================================================\n")

    # 2. 파라미터 또는 발화에서 이미지 URL 파싱 (케이스 확장)
    image_url = extract_image_url(params, user_request, utterance)

    try:
        member_record = sheets_client.get_member_by_userkey(userkey)
    except SheetReadError as e:
        print(f"[{request_id}] ❌ Member lookup failed: {e}")
        return build_kakao_response(
            "⏳ 현재 시트 조회가 지연되고 있어 잠시 후 다시 시도해 주세요.\n"
            "10~20초 뒤 '인증' 또는 사진을 다시 보내주시면 바로 이어서 처리됩니다."
            + BUTTON_FALLBACK_HINT
        )
    
    now = datetime.now()
    target_date = check_in_engine.get_target_date(now)

    if not member_record:
        # [자동 회원가입 로직]
        # 이미지를 보냈거나, 텍스트가 너무 길거나(15자), 하단 퀵리플라이 버튼을 누른 경우 가입 안내 문구 발송
        is_button_click = utterance in RESERVED_NICK_INPUTS
        
        if image_url or len(utterance) > 15 or is_button_click:
            return build_kakao_response(
                "✨ 환영합니다! 평일 저녁 인증 스터디 봇입니다.\n"
                "구루미 닉네임 = 오픈채팅방 닉네임과 동일하게 등록합니다.\n\n"
                "사용하실 닉네임만 채팅창에 짧게 입력해 주세요!\n"
                "(예: 키뮤)"
            )
        
        # 그 외의 짧은 텍스트는 닉네임으로 간주하여 즉시 등록
        target_nick = utterance.strip()
        nick_error = get_nickname_validation_error(target_nick)
        if nick_error:
            return build_kakao_response(
                f"⚠️ 닉네임 등록이 필요합니다.\n({nick_error})\n\n"
                "구루미 닉네임을 15자 이하로 입력해 주세요.\n"
                "(예: 키뮤)"
            )
        if is_duplicate_nickname(userkey, target_nick):
            return build_kakao_response(
                "⚠️ 이미 사용 중인 닉네임입니다.\n"
                "다른 닉네임으로 다시 입력해 주세요."
            )
        new_row = [target_nick, userkey, "대기", "2시간 0분", "0시간 0분", "1.0", "1", "10000", "-", "-", target_date, "불가", "-"]
        append_ok = sheets_client.append_row("Member_Master", new_row)
        if not append_ok:
            print(f"[{request_id}] ❌ 회원가입 append_row 실패 userkey={userkey}, nickname={target_nick}")
            return build_kakao_response("❌ 회원가입 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.")
        
        return build_kakao_response(
            f"✅ '{target_nick}'님, 가입이 완료되었습니다!\n"
            f"(기본 혜택: 주휴 1회, 월휴 1회)\n\n"

            f"하단의 메뉴 버튼을 이용해 인증을 시작해 보세요."
        )
        
    row_idx = member_record.get("_row_index", -1)
    nickname = str(member_record.get("닉네임", "")).strip()
    member_status = str(member_record.get("상태", "")).strip()

    if member_status == "예치금 소진":
        depletion_date_str = str(member_record.get("예치금소진일자", "")).strip()
        if depletion_date_str and depletion_date_str != "-":
            try:
                depletion_date = datetime.strptime(depletion_date_str, "%Y-%m-%d").date()
                if datetime.now().date() > (depletion_date + timedelta(days=3)):
                    end_ok = sheets_client.update_cell("Member_Master", row_idx, 3, "스터디 종료")
                    if end_ok:
                        member_record["상태"] = "스터디 종료"
                        member_status = "스터디 종료"
            except ValueError:
                pass

    # [중요] 기존에 빈 닉네임으로 등록된 사용자는 다른 기능 진입 전에 닉네임부터 강제 등록
    if not nickname:
        if image_url or not utterance or utterance in RESERVED_NICK_INPUTS:
            return build_kakao_response(
                "⚠️ 닉네임 등록이 아직 완료되지 않았습니다.\n\n"
                "구루미 닉네임을 먼저 채팅창에 입력해 주세요.\n"
                "(예: 키뮤)"
            )

        nick_error = get_nickname_validation_error(utterance)
        if nick_error:
            return build_kakao_response(
                f"⚠️ 닉네임으로 사용할 수 없는 입력입니다.\n({nick_error})\n\n"
                "구루미 닉네임을 다시 입력해 주세요."
            )
        if is_duplicate_nickname(userkey, utterance):
            return build_kakao_response(
                "⚠️ 이미 사용 중인 닉네임입니다.\n"
                "다른 닉네임으로 다시 입력해 주세요."
            )

        update_ok = sheets_client.update_cell("Member_Master", row_idx, 1, utterance.strip())
        if not update_ok:
            print(f"[{request_id}] ❌ 빈 닉네임 보정 실패 userkey={userkey}, row_idx={row_idx}, nickname={utterance.strip()}")
            return build_kakao_response("❌ 닉네임 등록 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.")

        return build_kakao_response(
            f"✅ 닉네임이 '{utterance.strip()}'으로 등록되었습니다!\n\n"
            "이제 하단 메뉴 버튼으로 인증을 진행해 주세요."
        )

    # [목표 시간 변경] "목표변경 3시간" 또는 버튼 클릭
    utterance_clean = utterance.replace(" ", "")
    
    # --- [State 조회] 이전 버튼 클릭 상태 확인 ---
    state = user_states.get(userkey)
    is_state_valid = state and state["expires"] > datetime.now()
    
    # --- [목표변경 상태 처리] 이전에 "목표 변경" 버튼을 누른 상태에서 숫자만 입력한 경우 ---
    if is_state_valid and state["type"] == "목표변경" and not image_url:
        # "목표변경" 상태에서 들어온 텍스트를 시간값으로 처리
        del user_states[userkey]
        new_target_minutes = parse_duration_to_min(utterance)
        # 순수 숫자만 입력한 경우 (예: "100" → 100분)
        if new_target_minutes == 0:
            nums = re.findall(r'\d+', utterance)
            if nums:
                new_target_minutes = int(nums[0])
        if new_target_minutes < 120:
            return build_kakao_response("❌ 목표시간은 최소 2시간(120분) 이상부터 입력 가능합니다.")
        sheets_client.update_cell("Member_Master", row_idx, 4, format_min_to_str(new_target_minutes))
        return build_kakao_response(f"✅ 목표 시간이 '{format_min_to_str(new_target_minutes)}'으로 변경 적용되었습니다!")
    
    # --- [목표변경 직접 입력] "목표변경 3시간" 처럼 값을 함께 보낸 경우 ---
    if utterance_clean.startswith("목표변경") or utterance_clean.startswith("목표시간") or utterance_clean.startswith("목표설정"):
        nums = re.findall(r'\d+', utterance)
        if not nums:
            user_states[userkey] = {"type": "목표변경", "expires": datetime.now() + timedelta(minutes=5)}
            return build_kakao_response("🎯 목표시간 설정을 원하시나요?\n\n채팅창에 변경하실 시간과 함께 아래 양식으로 입력해 주세요!\n\n(예시)\n👉 목표변경 2시간 30분\n👉 목표시간 120\n👉 목표변경 3시간")
            
        new_target_minutes = parse_duration_to_min(utterance)
        if new_target_minutes < 120:
            return build_kakao_response("❌ 목표시간은 최소 2시간(120분) 이상부터 입력 가능합니다.")
            
        sheets_client.update_cell("Member_Master", row_idx, 4, format_min_to_str(new_target_minutes))
        return build_kakao_response(f"✅ 목표 시간이 '{format_min_to_str(new_target_minutes)}'으로 변경 적용되었습니다!")
    
    # --- ["목표 변경" 버튼 클릭 (숫자 없이)] ---
    if utterance == "목표 변경":
        user_states[userkey] = {"type": "목표변경", "expires": datetime.now() + timedelta(minutes=5)}
        return build_kakao_response("🎯 목표시간 설정을 원하시나요?\n\n채팅창에 변경하실 시간과 함께 아래 양식으로 입력해 주세요!\n\n(예시)\n👉 목표변경 2시간 30분\n👉 목표시간 120\n👉 목표변경 3시간")

    # 3. 사용자 발화(또는 Block명)로 인증/휴무 종류 분기 처리
    block_name = user_request.get("block", {}).get("name", "")
    
    is_half_off = "반휴" in utterance or "반휴" in block_name
    is_week_off = "주휴" in utterance or "주휴" in block_name
    is_month_off = "월휴" in utterance or "월휴" in block_name
    is_special_off = "특휴" in utterance or "특휴" in block_name
    is_status = "내 현황" in utterance or "현황" in block_name
    is_auth = utterance == "인증"

    if member_status == "스터디 종료":
        return build_kakao_response(
            "⛔ 현재 상태는 '스터디 종료'입니다.\n"
            "재참여가 필요하시면 관리자에게 문의해 주세요."
        )

    if member_status == "예치금 소진" and not is_status:
        depletion_date_str = str(member_record.get("예치금소진일자", "")).strip()
        deadline_text = "-"
        if depletion_date_str and depletion_date_str != "-":
            try:
                depletion_date = datetime.strptime(depletion_date_str, "%Y-%m-%d").date()
                deadline_date = depletion_date + timedelta(days=3)
                deadline_text = deadline_date.strftime("%Y-%m-%d")
            except ValueError:
                deadline_text = "-"

        return build_kakao_response(
            "⚠️ 현재 상태는 '예치금 소진'입니다.\n"
            f"소진일: {depletion_date_str if depletion_date_str else '-'}\n"
            f"입금 마감일: {deadline_text} (자정 전까지)\n"
            "해당 날짜 안에 예치금을 입금해 주세요.\n"
            "미입금 시 자동으로 스터디 종료 처리됩니다.\n"
            "추가 예치금 입금 후 관리자 확인이 완료되어야 스터디 참여가 가능합니다."
        )
    
    # --- [핵심] 명시적 버튼 클릭 시 이전 상태 무조건 초기화 ---
    # 유저가 새로운 의도를 표명했으므로, 이전에 기억해둔 상태(반휴 대기, 특휴 대기 등)를 즉시 삭제합니다.
    is_explicit_action = is_half_off or is_week_off or is_month_off or is_special_off or is_status or is_auth
    if is_explicit_action and userkey in user_states:
        del user_states[userkey]

    # 💡 [State 조회 및 적용]
    # 사진만 보냈더라도, 10분 내에 누른 버튼(반휴/특휴)이 있다면 해당 상태로 강제 지정합니다.
    # (위에서 명시적 버튼 클릭 시 이미 초기화했으므로, 여기서 적용되는 건 "사진만 보낸 경우"뿐)
    state = user_states.get(userkey)
    if state and state["expires"] > now:
        if state["type"] == "반휴":
            is_half_off = True
        elif state["type"] == "특휴":
            is_special_off = True
        
        # 실제로 사진이 들어와서 인증 처리가 시작되면, 대기 상태를 소진(삭제)합니다.
        if image_url:
            del user_states[userkey]

    # 💡 [액션별 허용 시간 체크]
    if is_status:
        action_type = "status"
    elif is_week_off:
        action_type = "week_off"
    elif is_month_off:
        action_type = "month_off"
    elif is_special_off:
        action_type = "special_off"
    else:
        # 일반 인증/반휴 인증 처리
        action_type = "general_auth"

    if not check_in_engine.is_action_allowed(action_type, now):
        if action_type in ("week_off", "month_off", "special_off"):
            return build_kakao_response("❌ 처리 가능 시간이 지났습니다.\n(주휴/월휴/특휴 마감: 익일 12:00, 오픈: 당일 17:00)")
        return build_kakao_response("❌ 처리 기간이 지났습니다.\n(일반/반휴 마감: 익일 02:00, 오픈: 당일 17:00)")

    # 💡 [휴무일(자율참여) 우선 차단]
    admin_events = sheets_client.get_sheet_records("Admin_Config")
    is_optional_day = is_optional_participation_day(admin_events, target_date)
                
    if is_optional_day and not is_status:
        return build_kakao_response("🏖️ 오늘은 [자율참여(휴무일)] 지정일입니다!\n\n거짓 인증, 휴가(반휴/주휴) 차감 등 일체의 스터디 인증이 필요하지 않습니다. 마음 편히 쉬시거나 자율적으로 공부해주세요! 🎉")

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
        
        # --- [당일 전환 로직] 새 요청이 가능한 경우에만 이전 차감을 환불하고 전환 ---
        validation_record = preview_member_record_after_refund(target_date, nickname, member_record)
        is_approved, msg, deduct_amt = check_in_engine.process_leave_request(validation_record, leave_type)
        
        if is_approved:
            previous_log_row = get_existing_daily_log_row(target_date, nickname)
            refund_updates, refund_msg, refunded_record = build_refund_member_updates(target_date, nickname, member_record)
            col_idx = 6 if leave_type == "주휴" else 7 # 6: 주간휴무, 7: 남은월휴
            leave_key = "주간휴무" if leave_type == "주휴" else "남은월휴"
            old_val_str = refunded_record.get(leave_key, "0")
            new_val = max(0.0, float(old_val_str) - deduct_amt)
            
            # 로그 반영 (당일 기록 Override 적용)
            weekly_deduct = deduct_amt if leave_type == "주휴" else 0.0
            monthly_deduct = deduct_amt if leave_type == "월휴" else 0.0
            log_row = build_daily_log_row(
                target_date=target_date,
                nickname=nickname,
                auth_type=leave_type,
                status_msg="PASS",
                approval="-",
                daily_time="0",
                total_time="-",
                penalty=0,
                image_id="-",
                weekly_deduct=weekly_deduct,
                monthly_deduct=monthly_deduct,
            )
            member_updates = refund_updates + [(col_idx, leave_key, str(new_val))]
            if commit_daily_log_and_member_updates(
                sheets_client, row_idx, member_record, log_row, member_updates, previous_log_row
            ):
                activate_member_if_needed(row_idx, member_record, source=f"{leave_type}_request")
                msg += refund_msg
            else:
                msg = "시트 저장 중 오류가 발생했습니다. 기존 기록과 잔여 휴무는 변경하지 않았습니다. 잠시 후 다시 시도해 주세요."
            
        reply_text = msg

    elif is_special_off:
        # [특휴 요청] - 관리자 승인 대기
        if not image_url:
            # 특휴를 누르고 아직 사진을 안 보냈으므로 상태 기억!
            user_states[userkey] = {"type": "특휴", "expires": now + timedelta(minutes=10)}
            reply_text = "🏥 특휴 처리를 위해 처방전이나 수험표 등의 증빙 사진을 지금 전송해 주세요."
        else:
            try:
                previous_log_row = get_existing_daily_log_row(target_date, nickname)
                refund_updates, refund_msg, _ = build_refund_member_updates(target_date, nickname, member_record)
                drive_url = image_url # 카카오 사진 원본 링크를 직접 사용 (구글 드라이브 업로드 생략)
                
                # '대기', 승인여부 'N' (기존 기록이 있다면 덮어쓰기)
                log_row = build_daily_log_row(
                    target_date=target_date,
                    nickname=nickname,
                    auth_type="특휴",
                    status_msg="대기",
                    approval="N",
                    daily_time="-",
                    total_time="-",
                    penalty=0,
                    image_id=drive_url,
                    weekly_deduct=0.0,
                    monthly_deduct=0.0,
                )
                if commit_daily_log_and_member_updates(
                    sheets_client, row_idx, member_record, log_row, refund_updates, previous_log_row
                ):
                    activate_member_if_needed(row_idx, member_record, source="special_off_submit")
                    reply_text = "🏥 특휴 증빙 사진이 정상 접수되었습니다. 방장 확인(승인) 전까지는 대기 상태가 유지됩니다." + refund_msg
                else:
                    reply_text = "시트 저장 중 오류가 발생했습니다. 기존 기록과 잔여 휴무는 변경하지 않았습니다. 잠시 후 다시 시도해 주세요."
            except Exception as e:
                reply_text = f"이미지 업로드 중 에러 발생: {e}"

    else:
        # [일반 인증 / 반휴 인증] (이미지가 들어왔거나 요청하는 경우)
        if not image_url:
            if is_half_off:
                # 반휴 누르고 아직 사진 안 보냈으므로 상태 기억!
                user_states[userkey] = {"type": "반휴", "expires": now + timedelta(minutes=10)}
                reply_text = "🌗 오늘 최소 1시간을 달성한 구루미 출석표 사진을 보내주세요. 본인 닉네임, 출석 날짜·시각, 공부시간, 누적시간이 같은 행에 보여야 합니다. 타이머 팝업은 인정되지 않습니다."
            else:
                reply_text = (
                    "🔥 구루미 출석표 사진을 보내주세요. 본인 닉네임, 출석 날짜·시각, 공부시간, 누적시간이 같은 행에 보여야 합니다. 타이머 팝업은 인정되지 않습니다."
                    + BUTTON_FALLBACK_HINT
                )
        else:
            auth_type = "반휴" if is_half_off else "일반"
            target_override = None
            pending_deduct_amt = 0
            
            # 반휴일 경우 우선 잔여휴무 검증
            if auth_type == "반휴":
                validation_record = preview_member_record_after_refund(target_date, nickname, member_record)
                is_approved, msg, deduct_amt = check_in_engine.process_leave_request(validation_record, "반휴")
                if not is_approved:
                    return build_kakao_response(msg)
                
                target_override = 1 # 반휴는 목표 1시간으로 고정
                pending_deduct_amt = deduct_amt # 검증 통과 시 차감하기 위해 보류

            # 🚀 [카카오 5초 타임아웃 완전 회피]
            # 사진 다운로드 + OCR + 시트 기록을 전부 백그라운드로 넘기고,
            # 카카오에게는 즉시 "접수 완료!" 응답을 1초 이내에 반환합니다.
            background_tasks.add_task(
                process_photo_auth_in_background,
                request_id, image_url, auth_type, nickname,
                dict(member_record), row_idx, target_date,
                target_override, pending_deduct_amt, now, callback_url
            )

            if callback_url:
                return build_kakao_callback_wait_response()
            
            import urllib.parse
            encoded_nick = urllib.parse.quote(nickname)
            dashboard_url = f"{request.base_url}dashboard?user={encoded_nick}"

            reply_text = (
                f"📸 [{auth_type}] 인증 사진 접수 완료!\n\n"
                f"아직 인증이 확정되지 않았습니다. 현재 결과 알림 연결이 없어 약 15초 후 아래 링크에서 판정과 거절 사유를 확인해주세요.\n\n"
                f"🔗 {dashboard_url}"
                f"{BUTTON_FALLBACK_HINT}"
            )

    print(f"📨 [카카오 응답 전송]: {reply_text[:100]}...")
    return build_kakao_response(reply_text)
