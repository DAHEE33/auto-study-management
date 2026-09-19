from datetime import datetime
from types import SimpleNamespace

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
                       "사진누적", "벌금액", "이미지ID", "차감주휴", "차감월휴"]
            return [dict(zip(headers, self.daily))]
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


def _run_background(monkeypatch, sheets, ocr_result, callback_url=""):
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
        "req", "https://image", "일반", "산들바람", dict(sheets.member), 2,
        "2026-09-09", None, 0, datetime(2026, 9, 9, 20, 0), callback_url,
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
    old = ["2026-09-09", "산들바람", "일반", "PASS", "-", "2시간 0분", "100시간 0분", "0", "old"]
    sheets = FakeSheets(total="100시간 0분", daily=old)
    message, _ = _run_background(monkeypatch, sheets, OCRResult(
        "2026-09-09 20:00:00", 121, total, ""
    ))
    assert message.startswith("인증 거절")
    assert sheets.daily[3] == "누적거절"
    assert sheets.member["최종누적"] == "100시간 0분"
    assert sheets.history[0][3] == "누적거절"


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
    assert "-5,000원" in message
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


def test_latest_total_recheck_prevents_older_completion_overwrite(monkeypatch):
    sheets = FakeSheets(total="100시간 0분")
    first, _ = _run_background(monkeypatch, sheets, OCRResult(
        "2026-09-09 20:00:00", 130, 8000, ""
    ))
    second, _ = _run_background(monkeypatch, sheets, OCRResult(
        "2026-09-09 19:00:00", 121, 7000, ""
    ))
    assert first.startswith("인증 완료")
    assert second.startswith("인증 거절")
    assert sheets.member["최종누적"] == "133시간 20분"


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
