from datetime import datetime
from types import SimpleNamespace
import asyncio
import copy
import json

import pytest

from jobs.daily_summary import _build_cell_value
from services.check_in_engine import CheckInEngine
from services.ocr_service import OCRResult, OCRService
from services.settlement_engine import SettlementEngine


def test_sandlebaram_regression_with_space_between_number_and_unit():
    result = OCRService.parse_attendance_rows(
        ["산들바람 | 2026-09-09 17:27:52 | 2시간 1분 | 289 시간 13분"],
        "산들바람",
    )
    assert result.succeeded
    assert result.daily_minutes == 121
    assert result.total_minutes == 17_353


def test_multiple_rows_use_only_nickname_row_and_preserve_column_order():
    result = OCRService.parse_attendance_rows([
        "다른사람 2026-09-09 17:01:00 9시간 0분 500시간 0분",
        "three(2h) 2026-09-09 17:27:52 289 시간 13분 2시간 1분",
    ], "three")
    assert (result.daily_minutes, result.total_minutes) == (17_353, 121)


@pytest.mark.parametrize("rows,nickname", [
    (["산들바람 02:01 289시간 13분"], "산들바람"),
    (["산들바람 2시간 1분 289시간 13분"], "산들바람"),
    (["다른사람 2026-09-09 17:27:52 2시간 1분 289시간 13분"], "산들바람"),
    ([
        "three(2h) 2026-09-09 17:27:52 2시간 1분 289시간 13분",
        "three-study 2026-09-09 18:27:52 3시간 1분 300시간 13분",
    ], "three"),
])
def test_popup_missing_date_nickname_mismatch_and_duplicate_are_rejected(rows, nickname):
    assert not OCRService.parse_attendance_rows(rows, nickname).succeeded


def test_zero_duration_is_a_successful_read():
    result = OCRService.parse_attendance_rows(
        ["zero 2026-09-09 17:27:52 0시간 0분 100시간 0분"], "zero"
    )
    assert result.succeeded
    assert result.daily_minutes == 0


def test_coordinate_rows_keep_horizontal_cells_together():
    def annotation(text, x, y):
        vertices = [SimpleNamespace(x=x, y=y), SimpleNamespace(x=x + 30, y=y),
                    SimpleNamespace(x=x + 30, y=y + 12), SimpleNamespace(x=x, y=y + 12)]
        return SimpleNamespace(description=text, bounding_poly=SimpleNamespace(vertices=vertices))
    annotations = [
        annotation("other", 0, 10), annotation("2026-09-09", 100, 10), annotation("17:00:00", 200, 10),
        annotation("1시간", 300, 10), annotation("1분", 350, 10), annotation("100시간", 430, 10), annotation("0분", 500, 10),
        annotation("three(2h)", 0, 50), annotation("2026-09-09", 100, 50), annotation("17:27:52", 200, 50),
        annotation("2시간", 300, 50), annotation("1분", 350, 50), annotation("289", 430, 50), annotation("시간", 465, 50), annotation("13분", 510, 50),
    ]
    rows = OCRService._rows_from_annotations(annotations)
    result = OCRService.parse_attendance_rows(rows, "three")
    assert (result.daily_minutes, result.total_minutes) == (121, 17_353)


def test_uninitialized_ocr_never_returns_success(monkeypatch):
    service = OCRService.__new__(OCRService)
    service.client = None
    assert not service.extract_time_from_image("unused", "user").succeeded


def test_date_validation_distinguishes_past_format_and_midnight_rule():
    engine = CheckInEngine()
    past = engine.validate_ocr_attendance("2026-09-09", "2026-09-08 23:30:00", 120, 120)
    invalid = engine.validate_ocr_attendance("2026-09-09", "23:30", 120, 120)
    midnight = engine.validate_ocr_attendance("2026-09-09", "2026-09-10 00:30:00", 120, 120)
    assert past.is_past_date and not past.valid
    assert not invalid.is_past_date and not invalid.valid
    assert midnight.valid and midnight.is_ontime
    assert SettlementEngine().calculate_penalty(120, 120, False, False, past.is_past_date) == -5000


def test_failed_daily_log_is_not_rendered_as_attendance():
    assert _build_cell_value({"유형": "일반", "판정": "OCR실패", "벌금액": "0"}) == "-"
    assert _build_cell_value({"유형": "일반", "판정": "PASS", "벌금액": "0"}) == "o"


class FakeSheets:
    def __init__(self, total="100시간 0분", daily=None, history_ok=True, log_ok=True):
        self.member = {
            "닉네임": "산들바람", "UserKey": "U1", "상태": "활동", "목표시간": "120",
            "최종누적": total, "주간휴무": "1.0", "남은월휴": "1", "예치금": "10000",
            "_row_index": 2,
        }
        self.daily = daily
        self.history = []
        self.history_ok = history_ok
        self.log_ok = log_ok
        self.update_calls = []

    def get_sheet_records(self, sheet):
        if sheet == "Daily_Log" and self.daily:
            headers = ["날짜", "닉네임", "유형", "판정", "승인여부(특휴시)", "당일시간",
                       "사진누적", "벌금액", "이미지ID", "차감주휴", "차감월휴", "당일인증정보"]
            return [dict(zip(headers, self.daily))]
        if sheet == "Photo_Auth_History":
            headers = ["날짜", "닉네임", "유형", "판정", "승인여부(특휴시)", "당일시간",
                       "사진누적", "벌금액", "이미지ID", "제출시각", "처리사유"]
            return [dict(zip(headers, row)) for row in self.history]
        return []
    def clear_cache(self, *_): pass
    def get_member_by_userkey(self, _): return dict(self.member)
    def get_today_auth_history(self, *_):
        if not self.daily:
            return {}
        return {"prev_type": self.daily[2], "prev_status": self.daily[3], "prev_duration": 120,
                "prev_weekly_deduct": self.daily[9] if len(self.daily) > 9 else 0,
                "prev_monthly_deduct": self.daily[10] if len(self.daily) > 10 else 0}
    def get_daily_penalty(self, *_): return int(self.daily[7]) if self.daily else 0
    def update_cell(self, _, row, col, value):
        self.update_calls.append((row, col, value))
        fields = {3: "상태", 5: "최종누적", 6: "주간휴무", 7: "남은월휴", 8: "예치금", 13: "예치금소진일자"}
        if col in fields:
            self.member[fields[col]] = str(value)
        return True
    def upsert_daily_log(self, row):
        if self.log_ok:
            self.daily = list(row)
        return self.log_ok
    def append_row(self, sheet, row):
        if sheet == "Photo_Auth_History" and self.history_ok:
            self.history.append(list(row))
        return self.history_ok


def _run_background(monkeypatch, sheets, ocr_result, callback_url="", auth_type="일반"):
    import integrations.google_sheets as sheets_module
    import routers.webhook as webhook
    import services.ocr_service as ocr_module

    monkeypatch.setattr(sheets_module, "sheets_client", sheets)
    monkeypatch.setattr(ocr_module, "ocr_service", SimpleNamespace(extract_time_from_image=lambda *_: ocr_result))
    monkeypatch.setattr(webhook.httpx, "get", lambda *_args, **_kwargs: SimpleNamespace(
        content=b"image", raise_for_status=lambda: None
    ))
    callbacks = []
    monkeypatch.setattr(webhook.httpx, "post", lambda url, **kwargs: callbacks.append((url, kwargs)) or SimpleNamespace(
        raise_for_status=lambda: None, json=lambda: {"status": "SUCCESS"}
    ))
    message = webhook.process_photo_auth_in_background(
        "req", "https://image", auth_type, "산들바람", dict(sheets.member), 2,
        "2026-09-09", 1 if auth_type == "반휴" else None,
        0.5 if auth_type == "반휴" else 0, datetime(2026, 9, 9, 20, 0), callback_url,
        "https://study.hee-factory.com/dashboard?user=%EC%82%B0%EB%93%A4%EB%B0%94%EB%9E%8C",
    )
    return message, callbacks


def test_increasing_total_is_applied_and_large_jump_is_allowed(monkeypatch):
    sheets = FakeSheets(total="100시간 0분")
    message, _ = _run_background(monkeypatch, sheets, OCRResult(
        "2026-09-09 20:00:00", 121, 17_353, ""
    ))
    assert message.startswith("인증 완료")
    assert sheets.member["최종누적"] == "289시간 13분"
    assert sheets.daily[3] == "PASS"
    assert len(sheets.history) == 1


@pytest.mark.parametrize("total", [6000, 5999])
def test_equal_or_decreased_total_rejected_without_overwrite(monkeypatch, total):
    old = ["2026-09-08", "산들바람", "일반", "PASS", "-", "2시간 0분", "100시간 0분", "0", "old"]
    sheets = FakeSheets(total="100시간 0분", daily=old)
    message, _ = _run_background(monkeypatch, sheets, OCRResult(
        "2026-09-09 20:00:00", 121, total, ""
    ))
    assert message.startswith("인증 거절")
    assert sheets.daily[3] == "누적거절"
    assert sheets.member["최종누적"] == "100시간 0분"
    assert sheets.history[0][3] == "누적거절"


@pytest.mark.parametrize("duration", [60, 120])
def test_same_day_auth_type_switch_with_equal_total(monkeypatch, duration):
    sheets = FakeSheets()
    photo = OCRResult("2026-09-09 20:00:00", duration, 6120, "")
    _run_background(monkeypatch, sheets, photo)
    original_deposit = sheets.member["예치금"]
    for auth_type in ["반휴", "반휴", "일반"]:
        message, _ = _run_background(monkeypatch, sheets, photo, auth_type=auth_type)
        assert not message.startswith("인증 거절")
        assert sheets.daily[2] == auth_type
        assert sheets.daily[3] == ("결석(목표미달)" if auth_type == "일반" and duration < 120 else "PASS")
        assert float(sheets.member["주간휴무"]) == (0.5 if auth_type == "반휴" else 1.0)
        assert sheets.member["예치금"] == ("10000" if auth_type == "반휴" else original_deposit)
        assert sheets.member["최종누적"] == "102시간 0분"


def _webhook_request(monkeypatch, sheets, utterance, photo=None, pending_tasks=None, callback_url="", callbacks=None, current_dt=None):
    import integrations.google_sheets as sheets_module
    import routers.webhook as webhook
    import services.ocr_service as ocr_module

    class Clock(datetime):
        @classmethod
        def now(cls): return current_dt or cls(2026, 9, 9, 20, 0)

    class Request:
        base_url = "https://study.test/"
        async def json(self):
            return {"userRequest": {"user": {"id": "U1"}, "utterance": utterance, "callbackUrl": callback_url},
                    "action": {"detailParams": {"image": {"origin": "https://image"}} if photo else {}}}

    class Tasks:
        def __init__(self): self.tasks = []
        def add_task(self, fn, *args): self.tasks.append((fn, args))

    monkeypatch.setattr(webhook, "sheets_client", sheets)
    monkeypatch.setattr(sheets_module, "sheets_client", sheets)
    monkeypatch.setattr(webhook, "datetime", Clock)
    monkeypatch.setattr(webhook, "user_states", {})
    monkeypatch.setattr(webhook.leave_reset_service, "run_if_needed", lambda: None)
    monkeypatch.setattr(webhook.httpx, "get", lambda *_a, **_k: SimpleNamespace(content=b"image", raise_for_status=lambda: None))
    monkeypatch.setattr(ocr_module, "ocr_service", SimpleNamespace(extract_time_from_image=lambda *_: photo))
    tasks = Tasks()
    if callbacks is not None:
        monkeypatch.setattr(webhook, "_send_kakao_callback", lambda url, message, _id: callbacks.append((url, webhook.build_kakao_response(message))))
    response = asyncio.run(webhook.kakao_webhook(Request(), tasks))
    is_deferred = response.get("useCallback", False)
    if is_deferred:
        assert response == {"version": "2.0", "useCallback": True}
        assert sheets.daily is None
    if pending_tasks is not None:
        pending_tasks.extend(tasks.tasks)
    else:
        for fn, args in tasks.tasks:
            fn(*args)
    if is_deferred:
        return callbacks[-1][1]["template"]["outputs"][0]["simpleText"]["text"]
    return response["template"]["outputs"][0]["simpleText"]["text"]


def test_leave_callback_reports_pass_and_buttons(monkeypatch):
    for leave in ("주휴", "월휴"):
        sheets = FakeSheets()
        callbacks = []
        message = _webhook_request(monkeypatch, sheets, f"{leave} 사용",
                                   callback_url="https://callback.test", callbacks=callbacks)
        assert len(callbacks) == 1
        assert f"{leave} 사용 완료 · PASS" in message
        assert "2026-09-09" in message
        assert f"잔여 {leave}: 0회" in message
        other_leave = "월휴" if leave == "주휴" else "주휴"
        assert f"잔여 {other_leave}: 1회" in message
        assert sheets.daily[2:4] == [leave, "PASS"]
        buttons = callbacks[0][1]["template"]["quickReplies"]
        assert {"인증", "주휴 사용", "월휴 사용", "내 현황"} <= {button["messageText"] for button in buttons}

    sheets = FakeSheets()
    sheets.member["남은월휴"] = "0"
    callbacks = []
    message = _webhook_request(monkeypatch, sheets, "월휴 사용",
                               callback_url="https://callback.test", callbacks=callbacks)
    assert "월휴가 없습니다" in message
    assert message.startswith("❌ ")
    assert sheets.daily is None


def test_auth_prompt_and_deadline_are_direct_with_callback(monkeypatch):
    for command in ("인증", "반휴", "반휴 인증"):
        sheets = FakeSheets()
        callbacks, tasks = [], []
        message = _webhook_request(monkeypatch, sheets, command, pending_tasks=tasks,
                                   callback_url="https://callback.test", callbacks=callbacks)
        assert "출석표 사진을 보내주세요" in message
        if "반휴" in command:
            assert "최소 1시간" in message
        assert callbacks == [] and tasks == []
        assert sheets.daily is None and not sheets.update_calls

        message = _webhook_request(monkeypatch, sheets, command, pending_tasks=tasks,
                                   callback_url="https://callback.test", callbacks=callbacks,
                                   current_dt=datetime(2026, 9, 10, 7, 12))
        assert message.startswith("❌ 처리 기간이 지났습니다.")
        assert callbacks == [] and tasks == []
        assert sheets.daily is None and not sheets.update_calls


def test_photo_submission_still_uses_callback(monkeypatch):
    sheets = FakeSheets()
    callbacks = []
    message = _webhook_request(monkeypatch, sheets, "사진",
                               photo=OCRResult("2026-09-09 20:00:00", 120, 6120, ""),
                               callback_url="https://callback.test", callbacks=callbacks)
    assert message.startswith("✅ 인증 완료 · PASS")
    assert len(callbacks) == 1
    assert sheets.daily[2:4] == ["일반", "PASS"]


def test_leave_response_balances_include_refunds(monkeypatch):
    sheets = FakeSheets()
    _webhook_request(monkeypatch, sheets, "주휴 사용")
    message = _webhook_request(monkeypatch, sheets, "월휴 사용")
    assert "잔여 주휴: 1회" in message
    assert "잔여 월휴: 0회" in message
    message = _webhook_request(monkeypatch, sheets, "주휴 사용")
    assert "잔여 주휴: 0회" in message
    assert "잔여 월휴: 1회" in message


def test_response_status_markers():
    from routers.webhook import build_kakao_response

    for message, marker in (
        ("인증 완료 · PASS", "✅"), ("✅ 월휴 사용 완료 · PASS", "✅"),
        ("인증 거절: 날짜 불일치", "❌"), ("시간 부족: 당일 30분", "❌"),
        ("처리 실패: 시트 저장 실패", "❌"), ("⚠️ 닉네임 등록이 필요합니다.", "❌"),
        ("🌗 출석표 사진을 보내주세요.", "⏳"), ("🏥 특휴 승인 대기", "⏳"),
        ("✨ 내 현황을 확인하세요.", "⏳"),
    ):
        text = build_kakao_response(message)["template"]["outputs"][0]["simpleText"]["text"]
        assert text.startswith(marker + " ")
        assert not text.startswith(marker + " " + marker)


def test_photo_response_after_monthly_leave(monkeypatch):
    for auth_type, duration, weekly in (("일반", 120, "1"), ("반휴", 60, "0.5")):
        sheets = FakeSheets()
        _webhook_request(monkeypatch, sheets, "월휴 사용")
        message, callbacks = _run_background(monkeypatch, sheets, OCRResult(
            "2026-09-09 20:00:00", duration, 6120, ""
        ), "https://callback.test", auth_type=auth_type)
        text = callbacks[0][1]["json"]["template"]["outputs"][0]["simpleText"]["text"]
        assert text.startswith("✅ 인증 완료 · PASS\n")
        assert f"인증 유형: {auth_type}" in text
        assert "적용 날짜: 2026-09-09" in text
        assert f"당일 공부: {duration // 60}시간 0분" in text
        assert f"잔여 주휴: {weekly}회" in text
        assert "잔여 월휴: 1회" in text
        assert "이전 월휴 차감분 1.0이 환불되었습니다" in text
        assert sheets.daily[2:4] == [auth_type, "PASS"]
        assert float(sheets.member["남은월휴"]) == 1

    for photo, reason in (
        (OCRResult(None, None, None, "", "출석 날짜를 확인할 수 없습니다."), "출석 날짜를 확인할 수 없습니다."),
        (OCRResult("2026-09-09 20:00:00", 120, 6000, ""), "사진 누적시간이 이전 날짜 누적시간과 같거나 작습니다"),
    ):
        sheets = FakeSheets()
        _webhook_request(monkeypatch, sheets, "월휴 사용")
        _, callbacks = _run_background(monkeypatch, sheets, photo, "https://callback.test")
        text = callbacks[0][1]["json"]["template"]["outputs"][0]["simpleText"]["text"]
        assert text.startswith("❌ 인증 거절\n거절 사유: ")
        assert reason in text
        assert "적용 날짜: 2026-09-09" in text
        assert "환불되었습니다" not in text
        assert float(sheets.member["남은월휴"]) == 0


def test_photo_failure_response_explains_shortage_and_lateness(monkeypatch):
    for at, duration, reason in (
        ("2026-09-09 20:00:00", 60, "목표 공부시간을 달성하지 못했습니다."),
        ("2026-09-10 01:30:00", 60, "익일 01시 이후에도 목표 공부시간을 달성하지 못했습니다."),
    ):
        sheets = FakeSheets()
        _, callbacks = _run_background(monkeypatch, sheets, OCRResult(at, duration, 6120, ""), "https://callback.test")
        text = callbacks[0][1]["json"]["template"]["outputs"][0]["simpleText"]["text"]
        assert text.startswith("❌ 인증 실패 · 결석\n")
        assert f"실패 사유: {reason}" in text
        assert "벌금:" in text
        assert "당일 공부:" in text and "목표 시간:" in text
        assert "잔여 주휴:" in text and "잔여 월휴:" in text


def test_all_same_day_transitions_through_webhook(monkeypatch):
    choices = {"일반": "인증", "반휴": "반휴 인증", "주휴": "주휴 사용", "월휴": "월휴 사용"}
    for duration in (3, 60, 120):
        for source in choices:
            for destination in choices:
                sheets = FakeSheets()
                photo = OCRResult("2026-09-09 20:00:00", duration, 6000 + duration, "")
                # 사진을 먼저 보관한 뒤 모든 유형 쌍 및 중복 요청을 실제 라우터로 처리합니다.
                _webhook_request(monkeypatch, sheets, "인증", photo)
                for kind in (source, destination, destination):
                    _webhook_request(monkeypatch, sheets, choices[kind])
                    target = 60 if kind == "반휴" else 120
                    failed = kind in {"일반", "반휴"} and duration < target
                    penalty = (-1000 if duration < 60 else -500) if failed else 0
                    assert sheets.daily[2:4] == [kind, "결석(목표미달)" if failed else "PASS"], (source, destination, duration)
                    assert int(sheets.member["예치금"]) == 10000 + penalty
                    assert float(sheets.member["주간휴무"]) == (0 if kind == "주휴" else 0.5 if kind == "반휴" and not failed else 1)
                    assert float(sheets.member["남은월휴"]) == (0 if kind == "월휴" else 1)
                    assert webhook_minutes(sheets.daily[5]) == (0 if kind in {"주휴", "월휴"} else duration)
                    assert webhook_minutes(sheets.member["최종누적"]) == (6000 if kind in {"주휴", "월휴"} else 6000 + duration)
                    assert json.loads(sheets.daily[11])["baseline_total"] == 6000
                assert sheets.history[0][5] == f"{duration // 60}시간 {duration % 60}분"


def webhook_minutes(value):
    from routers.webhook import parse_duration_to_min
    return parse_duration_to_min(value)


def test_late_penalty_to_leave_and_back(monkeypatch):
    sheets = FakeSheets()
    photo = OCRResult("2026-09-10 01:30:00", 3, 6003, "")
    _webhook_request(monkeypatch, sheets, "인증", photo)
    assert sheets.member["예치금"] == "8000"
    _webhook_request(monkeypatch, sheets, "주휴 사용")
    assert sheets.member["예치금"] == "10000"
    assert float(sheets.member["주간휴무"]) == 0
    _webhook_request(monkeypatch, sheets, "인증")
    assert sheets.member["예치금"] == "8000"
    assert float(sheets.member["주간휴무"]) == 1


def test_leave_save_failure_restores_photo_and_balances(monkeypatch):
    sheets = FakeSheets()
    _webhook_request(monkeypatch, sheets, "인증", OCRResult("2026-09-09 20:00:00", 60, 6060, ""))
    before = copy.deepcopy((sheets.member, sheets.daily, sheets.history))
    original = sheets.update_cell
    failed = []
    def fail_once(sheet, row, col, value):
        if col == 6 and not failed:
            failed.append(True)
            return False
        return original(sheet, row, col, value)
    monkeypatch.setattr(sheets, "update_cell", fail_once)
    message = _webhook_request(monkeypatch, sheets, "주휴 사용")
    assert "오류" in message
    assert (sheets.member, sheets.daily, sheets.history) == before


def test_legacy_history_after_leave_allows_resubmission(monkeypatch):
    sheets = FakeSheets(total="102시간 0분", daily=["2026-09-09", "산들바람", "월휴", "PASS", "-", "0", "-", "0", "-", "0", "1"])
    sheets.member["남은월휴"] = "0"
    sheets.history = [["2026-09-09", "산들바람", "일반", "PASS", "-", "2시간 0분", "102시간 0분", "0", "https://image", "2026-09-09 20:00:00", "현재 최종 기록으로 적용했습니다."]]
    _webhook_request(monkeypatch, sheets, "반휴 인증", OCRResult("2026-09-09 20:00:00", 120, 6120, ""))
    assert sheets.daily[2:4] == ["반휴", "PASS"]
    assert float(sheets.member["남은월휴"]) == 1
    assert float(sheets.member["주간휴무"]) == 0.5


def test_pending_photo_cannot_overwrite_newer_leave(monkeypatch):
    sheets = FakeSheets()
    pending = []
    photo = OCRResult("2026-09-09 20:00:00", 120, 6120, "")
    _webhook_request(monkeypatch, sheets, "인증", photo, pending)
    _webhook_request(monkeypatch, sheets, "월휴 사용")
    import services.ocr_service as ocr_module
    monkeypatch.setattr(ocr_module, "ocr_service", SimpleNamespace(extract_time_from_image=lambda *_: photo))
    before = copy.deepcopy((sheets.member, sheets.daily))
    fn, args = pending[0]
    assert "이후 변경" in fn(*args)
    assert (sheets.member, sheets.daily) == before
    assert sheets.history[-1][3] == "처리취소"


def test_depleted_deposit_can_be_restored_by_same_day_leave(monkeypatch):
    sheets = FakeSheets()
    sheets.member["예치금"] = "500"
    _webhook_request(monkeypatch, sheets, "인증", OCRResult("2026-09-09 20:00:00", 3, 6003, ""))
    assert sheets.member["상태"] == "예치금 소진"
    _webhook_request(monkeypatch, sheets, "월휴 사용")
    assert sheets.member["상태"] == "활동"
    assert sheets.member["예치금"] == "500"
    assert sheets.member["예치금소진일자"] == "-"
    assert float(sheets.member["남은월휴"]) == 0


def test_failed_photo_then_leave_then_reuse_keeps_history(monkeypatch):
    sheets = FakeSheets()
    _webhook_request(monkeypatch, sheets, "반휴 인증", OCRResult("2026-09-09 20:00:00", 60, 6060, ""))
    _webhook_request(monkeypatch, sheets, "인증", OCRResult(None, None, None, "", "판독 오류"))
    assert sheets.daily[3] == "판독실패"
    _webhook_request(monkeypatch, sheets, "월휴 사용")
    assert float(sheets.member["주간휴무"]) == 1
    assert float(sheets.member["남은월휴"]) == 0
    _webhook_request(monkeypatch, sheets, "반휴 인증")
    assert sheets.daily[2:4] == ["반휴", "PASS"]
    assert float(sheets.member["주간휴무"]) == 0.5
    assert float(sheets.member["남은월휴"]) == 1
    assert [row[3] for row in sheets.history] == ["PASS", "판독실패", "PASS"]


def test_first_leave_requires_photo_and_unavailable_leave_keeps_state(monkeypatch):
    sheets = FakeSheets()
    _webhook_request(monkeypatch, sheets, "주휴 사용")
    before = copy.deepcopy((sheets.member, sheets.daily))
    assert "사진을 보내주세요" in _webhook_request(monkeypatch, sheets, "반휴 인증")
    assert (sheets.member, sheets.daily) == before
    _webhook_request(monkeypatch, sheets, "반휴 인증", OCRResult("2026-09-09 20:00:00", 60, 6060, ""))
    assert float(sheets.member["주간휴무"]) == 0.5
    sheets.member["남은월휴"] = "0"
    before = copy.deepcopy((sheets.member, sheets.daily))
    assert "월휴가 없습니다" in _webhook_request(monkeypatch, sheets, "월휴 사용")
    assert (sheets.member, sheets.daily) == before


def test_special_leave_pending_restores_previous_deductions(monkeypatch):
    sheets = FakeSheets()
    _webhook_request(monkeypatch, sheets, "반휴 인증", OCRResult("2026-09-09 20:00:00", 60, 6060, ""))
    _webhook_request(monkeypatch, sheets, "특휴 증빙하기", OCRResult(None, None, None, ""))
    assert sheets.daily[2:5] == ["특휴", "대기", "N"]
    assert float(sheets.member["주간휴무"]) == 1
    _webhook_request(monkeypatch, sheets, "반휴 인증")
    assert sheets.daily[2:4] == ["반휴", "PASS"]
    assert float(sheets.member["주간휴무"]) == 0.5


def test_missing_new_sheet_header_fails_without_writing():
    from integrations.google_sheets import GoogleSheetsClient, DAILY_LOG_HEADERS
    client = GoogleSheetsClient.__new__(GoogleSheetsClient)
    client.is_mock = False
    client._cache = {}
    client._cache_time = {}
    client.spreadsheet = SimpleNamespace(worksheet=lambda _: SimpleNamespace(row_values=lambda _: DAILY_LOG_HEADERS[:-1]))
    assert not client.upsert_daily_log([""] * len(DAILY_LOG_HEADERS))


def test_failed_resubmission_preserves_final_record_and_writes_history(monkeypatch):
    old = ["2026-09-09", "산들바람", "주휴", "PASS", "-", "0", "-", "0", "old"]
    sheets = FakeSheets(daily=old)
    message, _ = _run_background(monkeypatch, sheets, OCRResult(None, None, None, "", "사진 형식 오류"))
    assert "판독 실패로 기록" in message
    assert sheets.daily[3] == "판독실패"
    assert sheets.daily[9] == "1.0"
    assert sheets.history[0][3] == "판독실패"
    assert sheets.update_calls == []


def test_past_photo_penalty_and_existing_final_policy(monkeypatch):
    sheets = FakeSheets()
    message, _ = _run_background(monkeypatch, sheets, OCRResult(
        "2026-09-08 20:00:00", 121, 7000, ""
    ))
    assert "거절 사유: 과거 날짜 사진입니다." in message
    assert "벌금: 5,000원" in message
    assert sheets.daily[7] == "-5000"

    old = ["2026-09-09", "산들바람", "일반", "PASS", "-", "2시간 1분", "110시간 0분", "0", "old"]
    sheets = FakeSheets(daily=old)
    message, _ = _run_background(monkeypatch, sheets, OCRResult(
        "2026-09-08 20:00:00", 121, 7000, ""
    ))
    assert "기존 차감 내역은 유지" in message
    assert sheets.daily[3] == "과거사진"
    assert not sheets.update_calls


def test_callback_success_missing_and_history_storage_failure(monkeypatch):
    sheets = FakeSheets()
    message, callbacks = _run_background(monkeypatch, sheets, OCRResult(
        "2026-09-09 20:00:00", 121, 7000, ""
    ), "https://callback-secret")
    assert message.startswith("인증 완료") and len(callbacks) == 1
    assert callbacks[0][1]["json"]["version"] == "2.0"
    text = callbacks[0][1]["json"]["template"]["outputs"][0]["simpleText"]["text"]
    assert text.startswith("✅ 인증 완료 · PASS\n")
    assert "https://study.hee-factory.com/dashboard?user=%EC%82%B0%EB%93%A4%EB%B0%94%EB%9E%8C" in text
    assert "버튼이 안 보이면" in text
    assert "이상 확인 시 그 주 일요일까지만 수정 가능합니다." in text

    sheets = FakeSheets(history_ok=False)
    message, callbacks = _run_background(monkeypatch, sheets, OCRResult(None, None, None, "", "OCR 실패"))
    assert "이력을 저장하지 못했습니다" in message
    assert callbacks == []


def test_callback_transport_and_body_failure_do_not_repeat_auth(monkeypatch):
    import routers.webhook as webhook
    calls = []
    monkeypatch.setattr(webhook.httpx, "post", lambda *_args, **_kwargs: calls.append(1) or SimpleNamespace(
        raise_for_status=lambda: None, json=lambda: {"status": "FAIL"}
    ))
    assert not webhook._send_kakao_callback("https://secret", "done", "req")
    assert calls == [1]
    monkeypatch.setattr(webhook.httpx, "post", lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError()))
    assert not webhook._send_kakao_callback("https://secret", "done", "req")


def test_sheet_log_failure_never_reports_success(monkeypatch):
    sheets = FakeSheets(log_ok=False)
    message, _ = _run_background(monkeypatch, sheets, OCRResult(
        "2026-09-09 20:00:00", 121, 7000, ""
    ))
    assert message.startswith("처리 실패")
    assert sheets.member["최종누적"] == "100시간 0분"
    assert sheets.daily is None


def test_callback_wait_optional_day_and_dashboard_history_filter():
    from routers.dashboard import build_member_photo_history
    from routers.webhook import build_kakao_callback_wait_response, is_optional_participation_day

    response = build_kakao_callback_wait_response()
    assert response == {"version": "2.0", "useCallback": True}
    assert "template" not in response
    events = [{"날짜": "2026-09-24", "이벤트 타입": "자율참여"}]
    assert is_optional_participation_day(events, "2026-09-24")
    assert not is_optional_participation_day(events, "2026-09-23")

    history = [
        {"닉네임": "me", "날짜": "2026-09-09", "이미지ID": "new", "제출시각": "2026-09-09 20:00:00"},
        {"닉네임": "other", "날짜": "2026-09-09", "이미지ID": "other", "제출시각": "2026-09-09 21:00:00"},
        {"닉네임": "me", "날짜": "2026-09-09", "이미지ID": "old", "제출시각": "2026-09-09 19:00:00"},
    ]
    logs = [{"닉네임": "me", "날짜": "2026-09-09", "이미지ID": "new"}]
    result = build_member_photo_history(history, logs, "me")
    assert [item["이미지ID"] for item in result] == ["new", "old"]
    assert result[0]["관계"] == "현재 최종 기록"
    assert result[1]["관계"] == "이전 제출 기록"


def test_normal_leave_refund_rule_is_preserved(monkeypatch):
    import integrations.google_sheets as sheets_module
    import routers.webhook as webhook

    daily = ["2026-09-09", "산들바람", "주휴", "PASS", "-", "0", "-", "-500", "old"]
    sheets = FakeSheets(daily=daily)
    monkeypatch.setattr(sheets_module, "sheets_client", sheets)
    member = dict(sheets.member)
    member["주간휴무"] = "0.0"
    updates, message, preview = webhook.build_refund_member_updates("2026-09-09", "산들바람", member, sheets)
    assert sheets.update_calls == []
    ok, _ = webhook.apply_member_updates(sheets, 2, member, updates)
    assert ok
    assert preview["주간휴무"] == "1.0"
    assert "주간휴무 차감분 1.0" in message
    assert "기존 패널티 -500원" in message
    assert any(col == 6 for _, col, _ in sheets.update_calls)
    assert any(col == 8 for _, col, _ in sheets.update_calls)


def test_same_day_new_photo_uses_previous_day_baseline(monkeypatch):
    sheets = FakeSheets(total="100시간 0분")
    first, _ = _run_background(monkeypatch, sheets, OCRResult(
        "2026-09-09 20:00:00", 130, 8000, ""
    ))
    second, _ = _run_background(monkeypatch, sheets, OCRResult(
        "2026-09-09 19:00:00", 121, 7000, ""
    ))
    assert first.startswith("인증 완료")
    assert second.startswith("인증 완료")
    assert sheets.member["최종누적"] == "116시간 40분"
    third, _ = _run_background(monkeypatch, sheets, OCRResult(
        "2026-09-09 20:00:00", 121, 6000, ""
    ))
    assert third.startswith("인증 거절")
    assert sheets.member["최종누적"] == "116시간 40분"


def test_weekly_report_weekend_only_and_same_week_range(monkeypatch):
    import jobs.weekly_settlement as job
    class Clock(datetime):
        @classmethod
        def now(cls): return cls(2026, 9, 18, 12)
    calls, writes = [], []
    monkeypatch.setattr(job, "datetime", Clock)
    monkeypatch.setattr(job, "sheets_client", SimpleNamespace(
        get_sheet_records=lambda name: [], append_row=lambda *args: writes.append(args)))
    monkeypatch.setattr(job, "settlement_engine", SimpleNamespace(
        generate_weekly_report=lambda **kwargs: calls.append(kwargs) or "report"))
    job.run_weekly_settlement_job()
    assert not calls and not writes
    for day in (19, 20):
        monkeypatch.setattr(Clock, "now", classmethod(lambda cls, day=day: cls(2026, 9, day, 12)))
        job.run_weekly_settlement_job()
        assert calls[-1]["start_date"] == "2026-09-14"
        assert calls[-1]["end_date"] == "2026-09-18"


def test_rejected_popup_sends_callback_without_daily_log(monkeypatch):
    sheets = FakeSheets()
    message, callbacks = _run_background(monkeypatch, sheets,
        OCRResult(None, None, None, "", "출석표 사진이 아닙니다"), "https://callback")
    assert message.startswith("인증 거절")
    assert sheets.daily[3] == "판독실패" and len(sheets.history) == 1
    assert len(callbacks) == 1
    assert "인증 거절" in callbacks[0][1]["json"]["template"]["outputs"][0]["simpleText"]["text"]


def test_latest_failure_keeps_money_and_leave_for_later_refund(monkeypatch):
    import routers.webhook as webhook
    old = ["2026-09-09", "산들바람", "반휴", "PASS", "-", "1시간", "100시간", "-500", "old", "0.5", "0.0"]
    sheets = FakeSheets(daily=old)
    sheets.member["주간휴무"] = "0.5"
    sheets.member["예치금"] = "9500"
    for _ in range(2):
        _run_background(monkeypatch, sheets, OCRResult(None, None, None, "", "잘못된 사진"))
    assert sheets.daily[3] == "판독실패"
    assert sheets.daily[7:11] == ["-500", "https://image", "0.5", "0.0"]
    assert len(sheets.history) == 2 and not sheets.update_calls
    updates, _, preview = webhook.build_refund_member_updates("2026-09-09", "산들바람", sheets.member, sheets)
    assert preview["주간휴무"] == "1.0"
    assert preview["예치금"] == "10000"
    assert len(updates) == 2


def test_new_half_leave_failure_does_not_create_refund(monkeypatch):
    import routers.webhook as webhook
    sheets = FakeSheets()
    webhook.save_latest_photo_failure(sheets, "2026-09-09", "산들바람", "반휴", "판독실패", None, None, "image")
    sheets.member["주간휴무"] = "0.0"
    updates, _, preview = webhook.build_refund_member_updates("2026-09-09", "산들바람", sheets.member, sheets)
    assert updates == [] and preview["주간휴무"] == "0.0"


def test_weekday_submission_windows_and_friday_deadlines():
    engine = CheckInEngine()
    cases = [
        ("2026-09-18 17:00", True, True),
        ("2026-09-19 00:00", True, True),
        ("2026-09-19 01:59", True, True),
        ("2026-09-19 02:00", False, True),
        ("2026-09-19 11:59", False, True),
        ("2026-09-19 12:00", False, False),
        ("2026-09-19 17:00", False, False),
        ("2026-09-20 01:00", False, False),
        ("2026-09-20 17:00", False, False),
        ("2026-09-21 01:00", False, False),
        ("2026-09-21 16:59", False, False),
        ("2026-09-21 17:00", True, True),
    ]
    for raw, auth, leave in cases:
        now = datetime.strptime(raw, "%Y-%m-%d %H:%M")
        assert engine.is_action_allowed("general_auth", now) == auth, raw
        for action in ("week_off", "month_off", "special_off"):
            assert engine.is_action_allowed(action, now) == leave, (raw, action)
        assert engine.is_action_allowed("status", now)
