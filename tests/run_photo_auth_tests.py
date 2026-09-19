"""표준 라이브러리만으로 실행하는 사진 인증 회귀 테스트 러너.

외부 SDK는 import 경계에서 가짜 모듈로 대체하므로 네트워크/Google Sheets/카카오를 호출하지 않는다.
"""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def module(name, **attrs):
    value = types.ModuleType(name)
    for key, item in attrs.items():
        setattr(value, key, item)
    sys.modules[name] = value
    return value


class Dummy:
    def __init__(self, *args, **kwargs): pass
    def __call__(self, *args, **kwargs): return Dummy()
    def __getattr__(self, _): return Dummy()
    def __iter__(self): return iter(())


module("dotenv", load_dotenv=lambda **_: None)
google = module("google")
cloud = module("google.cloud")
vision = module("google.cloud.vision", ImageAnnotatorClient=Dummy, Image=Dummy)
google.cloud = cloud
cloud.vision = vision

gspread = module("gspread", authorize=lambda *_: Dummy())
gspread.exceptions = types.SimpleNamespace(WorksheetNotFound=Exception)
module("oauth2client")
module("oauth2client.service_account", ServiceAccountCredentials=types.SimpleNamespace(
    from_json_keyfile_name=lambda *_: Dummy()
))

class BackgroundTasks:
    def add_task(self, *args, **kwargs): pass

fastapi = module("fastapi", APIRouter=lambda *a, **k: Dummy(), Request=Dummy,
                 BackgroundTasks=BackgroundTasks, Query=lambda default=None, **_: default)
module("fastapi.responses", HTMLResponse=Dummy)
module("fastapi.templating", Jinja2Templates=Dummy)
module("httpx", get=Dummy(), post=Dummy(), AsyncClient=Dummy)
module("pydantic", BaseModel=object)
module("integrations.google_drive", drive_client=Dummy())

pytest = module("pytest")
pytest.mark = types.SimpleNamespace(parametrize=lambda *_args, **_kwargs: (lambda fn: fn))


class MonkeyPatch:
    def __init__(self): self.changes = []
    def setattr(self, target, name, value):
        self.changes.append((target, name, getattr(target, name)))
        setattr(target, name, value)
    def undo(self):
        for target, name, value in reversed(self.changes):
            setattr(target, name, value)


from tests import test_photo_auth as tests  # noqa: E402


def with_patch(fn, *args):
    patch = MonkeyPatch()
    try:
        fn(patch, *args)
    finally:
        patch.undo()


cases = [
    ("최신 실패/전체 이력/차감 보존", lambda: with_patch(tests.test_latest_failure_keeps_money_and_leave_for_later_refund)),
    ("실패 반휴의 허위 환불 방지", lambda: with_patch(tests.test_new_half_leave_failure_does_not_create_refund)),
    ("주말 정산/평일 집계", lambda: with_patch(tests.test_weekly_report_weekend_only_and_same_week_range)),
    ("팝업 거절 콜백", lambda: with_patch(tests.test_rejected_popup_sends_callback_without_daily_log)),
    ("OCR 공백 회귀", tests.test_sandlebaram_regression_with_space_between_number_and_unit),
    ("다중 행/열 순서", tests.test_multiple_rows_use_only_nickname_row_and_preserve_column_order),
    ("0시간과 실패 구분", tests.test_zero_duration_is_a_successful_read),
    ("OCR 좌표 행 조립", tests.test_coordinate_rows_keep_horizontal_cells_together),
    ("OCR 초기화 실패", lambda: with_patch(tests.test_uninitialized_ocr_never_returns_success)),
    ("날짜/자정/과거 벌금", tests.test_date_validation_distinguishes_past_format_and_midnight_rule),
    ("실패 집계 제외", tests.test_failed_daily_log_is_not_rendered_as_attendance),
    ("누적 증가/큰 증가", lambda: with_patch(tests.test_increasing_total_is_applied_and_large_jump_is_allowed)),
    ("누적 동일 거절", lambda: with_patch(tests.test_equal_or_decreased_total_rejected_without_overwrite, 6000)),
    ("누적 감소 거절", lambda: with_patch(tests.test_equal_or_decreased_total_rejected_without_overwrite, 5999)),
    ("실패 재제출 최신 반영", lambda: with_patch(tests.test_failed_resubmission_preserves_final_record_and_writes_history)),
    ("과거 사진 벌금/재제출", lambda: with_patch(tests.test_past_photo_penalty_and_existing_final_policy)),
    ("콜백/미제공/이력실패", lambda: with_patch(tests.test_callback_success_missing_and_history_storage_failure)),
    ("콜백 실패 중복방지", lambda: with_patch(tests.test_callback_transport_and_body_failure_do_not_repeat_auth)),
    ("시트 저장 실패", lambda: with_patch(tests.test_sheet_log_failure_never_reports_success)),
    ("콜백 대기/자율참여/이력 필터", tests.test_callback_wait_optional_day_and_dashboard_history_filter),
    ("휴무/벌금 환불", lambda: with_patch(tests.test_normal_leave_refund_rule_is_preserved)),
    ("완료 역순 덮어쓰기 방지", lambda: with_patch(tests.test_latest_total_recheck_prevents_older_completion_overwrite)),
]

invalid_rows = [
    (["산들바람 02:01 289시간 13분"], "산들바람"),
    (["산들바람 2시간 1분 289시간 13분"], "산들바람"),
    (["다른사람 2026-09-09 17:27:52 2시간 1분 289시간 13분"], "산들바람"),
    (["three(2h) 2026-09-09 17:27:52 2시간 1분 289시간 13분",
      "three-study 2026-09-09 18:27:52 3시간 1분 300시간 13분"], "three"),
]
for index, args in enumerate(invalid_rows, 1):
    cases.append((f"형식/닉네임/중복 거절 {index}", lambda args=args: tests.test_popup_missing_date_nickname_mismatch_and_duplicate_are_rejected(*args)))

failed = []
for name, case in cases:
    try:
        case()
        print(f"PASS {name}")
    except Exception as exc:
        failed.append((name, exc))
        print(f"FAIL {name}: {type(exc).__name__}: {exc}")

if failed:
    raise SystemExit(1)
print(f"{len(cases)} tests passed")
