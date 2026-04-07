# Project: Study-Sync (Auto-Settlement Engine)

> **"관리 리소스 Zero를 지향하는 스터디 운영 자동화 솔루션"**

본 프로젝트는 카카오톡 챗봇, OCR, Google Sheets API를 결합하여 출석 인증, 벌금 정산, 스토리지 관리를 자동화하는 **Serverless 정산 시스템**입니다.

---

## 🛠 Tech Stack
- **Language**: Python 3.11
- **Framework**: FastAPI
- **Cloud & DB**: Google Cloud Vision API (OCR), Google Sheets API (DB), Google Drive API (Storage)
- **CI/CD**: GitHub Actions (Cron Jobs)

---

## 📂 프로젝트 구조

```
auto_study_mn/
├── core/
│   └── config.py                 # .env 및 환경 변수 로드
├── integrations/
│   ├── google_sheets.py          # 구글 시트 CRUD 모듈 (벌금/로그 기록)
│   └── google_drive.py           # 구글 드라이브 이미지 업로드 담당
├── services/
│   ├── ocr_service.py            # 구글 비전 API를 이용한 인증 텍스트(시간) 파싱 로직
│   └── settlement_engine.py      # 익일 12시/새벽 1시 룰 검증 및 벌금 산정 코어 엔진
├── routers/
│   ├── webhook.py                # 카카오 i 챗봇 오픈빌더용 응답 JSON 엔드포인트
│   └── dashboard.py              # 통계 데이터 크롤링 및 웹뷰(Webview) 시각화 엔드포인트
├── jobs/
│   ├── daily_absence.py          # (배치) 매일 12시 결석자 판정 잡
│   └── admin_sync.py             # (배치) 관리자의 구글 시트 수동 승인 감지 잡
├── .github/workflows/
│   └── cron.yml                  # GitHub Actions 스케줄러 자동화 파일
├── .env                          # ⚠️ 로컬 환경 변수 (git 제외)
├── credentials.json              # ⚠️ GCP 서비스 계정 키 (git 제외)
├── README.md                     # 프로젝트 개요 명세서
└── TESTING.md                    # 통합 점검 및 시나리오 테스트 명세서
```

---

## 🚀 빠른 시작 (Quick Start)

### 1. 의존성 설치
```bash
pip install fastapi uvicorn gspread oauth2client python-dotenv google-api-python-client google-auth-httplib2 google-auth-oauthlib google-cloud-vision httpx
```

### 2. 구글 시트 및 드라이브 셋업
1. 복사해 둔 `.env` 파일에 `GOOGLE_SHEET_URL`, `GOOGLE_DRIVE_FOLDER_ID`를 설정합니다.
2. `credentials.json` GCP 서비스 계정 키를 프로젝트 루트에 위치시킵니다.
3. 최초 실행 시 (`python test_step1.py`) 구글 시트를 자동 스캔하여, 탭(Member_Master, Daily_Log)이 없다면 자동으로 초기 세팅을 진행합니다.

### 3. 실시간 서버 가동 및 카카오톡 실제 연동
```bash
uvicorn main:app --reload --port 8000
```
- 서버 구동 뒤 `http://localhost:8000/dashboard` 로 접속하면 누적 벌금, N빵 정산 정보가 포함된 대시보드를 열람할 수 있습니다.
- **실제 카카오톡 봇 연동 방법**: 위 서버를 켜둔 상태로 새 터미널에서 `ngrok http 8000`을 입력하여 외부 주소를 발급받은 뒤, 카카오 i 오픈빌더 스킬 서버 URL에 등록하면 핸드폰으로 실제 업로드 테스트가 시작됩니다! (상세 과정은 `TESTING.md` 참조)

---

## 👨‍💼 관리자 운영 가이드 (Admin Guide)

이 시스템은 별도의 복잡한 어드민 웹 페이지가 필요 없습니다. **Google Sheets 자체가 가장 강력하고 유연한 관리자 GUI(그래픽 유저 인터페이스) 역할을 합니다!**

### 1. 스터디원 관리 (Member_Master 시트)
- **멤버 추가/삭제**: 시트에서 직접 행(Row)을 추가하여 새로운 사람의 `닉네임`과 목표 시간(m)을 기입하시면 시스템이 즉시 새로운 멤버를 인식합니다.
- **예치금/목표 조정**: 특정 유저의 벌금 차감이나 이번 주 목표시간 변동이 필요할 경우, 시트에 적힌 글자를 직접 백스페이스로 지우고 숫자를 수정하시면 됩니다. 

### 2. 예외 승인 및 강제 벌금 조정 (Daily_Log 시트)
- **오류 강제 조정**: 억울하게 결석 처리된 사람이나 만점이 뜨지 않은 사람의 벌금 액수를 시트에서 직접 `0`으로 수정하시면 서버가 대시보드 화면 렌더링 시 이를 즉각 반영합니다.
- **특휴 승인 처리**: 멤버가 병가/예비군 등의 특휴 상황일 때 기입되는 `Pending` 상태를 관리자가 시트 상에서 `승인(TRUE)` 쪽으로 고쳐 적거나 체크박스에 체크합니다.
- **승인 동기화 스크립트 실행 (수동 푸시)**:
  ```bash
  python jobs/admin_sync.py
  ```
  이 스크립트를 실행하면 관리자가 시트에서 승인 처리한 멤버를 서버가 싹 찾아내서 "승인 완료 알림톡"을 전송하고 상태를 `Completed`로 전환합니다. (향후 cron 스케줄로 10분 단위 자동화 추천)
