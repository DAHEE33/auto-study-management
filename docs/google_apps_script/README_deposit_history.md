# Deposit History 자동적재 가이드

`Member_Master`(또는 `member_master`) 시트에서 예치금을 수정하면 `Deposit_History` 시트에 변경 이력이 자동 저장됩니다.

## 전제

- `Member_Master` 헤더에 아래 컬럼이 있어야 합니다.
  - `닉네임`
  - `UserKey`
  - `예치금`
- 예치금 수정은 가능한 **한 셀씩** 진행하세요.

## 적용 방법

1. 구글시트 상단 메뉴에서 `확장 프로그램 > Apps Script`를 엽니다.
2. 새 스크립트 파일을 만들고, `deposit_history_onedit.gs` 내용을 그대로 붙여넣습니다.
3. 저장 후 시트로 돌아가 `Member_Master`의 예치금을 한 번 수정해 동작을 확인합니다.
4. `Deposit_History` 시트가 자동 생성되고 첫 기록이 쌓이면 완료입니다.

## 반응이 없을 때 점검 순서

1. **시트명 확인**
   - 대상 시트명이 `Member_Master` 또는 `member_master`인지 확인
2. **수정 방식 확인**
   - 예치금 셀을 직접 클릭해서 숫자 입력 후 엔터 (한 셀씩)
3. **서버 로그와 무관**
   - 이 기능은 Apps Script에서 동작하므로 FastAPI 서버 로그에는 안 찍히는 것이 정상
4. **Apps Script 실행 기록 확인**
   - Apps Script 편집기 좌측 `실행`에서 실패 내역 확인
5. **에러 로그 시트 확인**
   - `Script_Error_Log` 시트가 생겼다면 마지막 행의 에러 메시지 확인
6. **수동 점검 함수 실행**
   - Apps Script에서 `testEnsureHistorySheet`를 1회 실행
   - `Deposit_History` 헤더 생성 여부 확인

## 기록 컬럼 설명 (`Deposit_History`)

- `기록시각`: 변경 기록 시간
- `UserKey`: 고정 사용자 식별자 (닉네임/정렬 변경 영향 없음)
- `닉네임_snapshot`: 기록 당시 닉네임
- `변경전예치금`, `변경후예치금`, `증감액`
- `이벤트유형`
  - `PRIZE_PAYOUT_RESET`: 10,000원 리셋
  - `DEPOSIT_INCREASE`: 예치금 증가
  - `DEPOSIT_DECREASE`: 예치금 감소
  - `UNKNOWN_OLDVALUE`: 이전값 미수신(복붙/수식 변경 가능)
- `수정셀(A1)`, `수정자`, `메모`

## 운영 규칙 권장

- `Member_Master`는 운영 대상만 유지하고, 스터디 종료자는 삭제 가능
- `Deposit_History`는 절대 삭제하지 않고 누적 보관
- 여러 셀 동시 붙여넣기는 이력 누락 가능성이 있어 지양
