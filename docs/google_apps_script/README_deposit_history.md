# Deposit History 자동적재 가이드

`Member_Master` 시트에서 예치금을 수정하면 `Deposit_History` 시트에 변경 이력이 자동 저장됩니다.

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
