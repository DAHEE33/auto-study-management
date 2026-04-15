# 🚀 Project: Study-Sync (Auto-Settlement Engine)

> **"관리 리소스 Zero를 지향하는 스터디 운영 자동화 솔루션"**
> 본 프로젝트는 카카오톡 챗봇, OCR, Google Sheets API를 결합하여 출석 인증, 벌금 정산, 스토리지 관리를 자동화하는 **Serverless 정산 시스템**입니다.

## 🛠 Tech Stack
- **Language**: Python 3.11
- **Framework**: FastAPI (Asynchronous)
- **Cloud & DB**: Google Cloud Vision API (OCR), Google Sheets API (DB), Google Drive API (Storage)
- **Infrastructure**: Railway (PaaS), 카카오 i 오픈빌더 스킬 연동
- **CI/CD**: GitHub Actions (Cron Jobs)

---

## 📂 프로젝트 구조 (리팩토링 기준)

```text
auto-study-management/
├── core/
│   └── config.py                 # .env, GCP 인증키 경로 등 환경 변수 관리
├── integrations/
│   ├── google_sheets.py          # Google Sheets CRUD (Member_Master, Admin_Config, Daily_Log)
│   └── google_drive.py           # 캡처 사진 Drive 업로드 및 삭제
├── services/
│   ├── image_service.py          # 카카오톡 서버 이미지 다운로드 및 최적화
│   ├── ocr_service.py            # 구글 비전 API (당일시간/누적시간 추출)
│   ├── check_in_engine.py        # 인증 로직 핵심 (05시~익일01시 판별, 반휴/주휴/월휴 스위칭 판단)
│   └── settlement_engine.py      # 주간 토요일 정오 결산, 제로섬 및 상금 계산, 벌금 산정
├── routers/
│   ├── webhook.py                # 카카오 i 챗봇 스킬 연동 분기 처리 (반휴/주휴/특휴 메뉴 등)
│   └── dashboard.py              # 필요 시 웹뷰 대시보드
├── jobs/
│   ├── weekly_settlement.py      # 매주 토요일 정오 주간 결산 자동화 배치
│   └── cleanup_images.py         # 14일 경과된 드라이브 인증 이미지 자동 삭제 (Zero Storage)
├── main.py                       # FastAPI 진입점 (Server)
├── .env                          # 로컬 구동용 환경변수 모음
├── credentials.json              # ⚠️ GCP 서비스 계정 키 (비공개)
└── README.md                     # 프로젝트 전체 개요
```

---

## 🏃 빠른 시작 (Quick Start)

### 1. 환경 설정
- `pip install fastapi uvicorn gspread oauth2client python-dotenv google-api-python-client google-auth-httplib2 google-auth-oauthlib google-cloud-vision httpx`
- 최상단 `credentials.json` GCP 서비스 계정 키 배치
- `.env`에 `GOOGLE_SHEET_URL`, `GOOGLE_DRIVE_FOLDER_ID` 세팅

### 2. 서버 구동
```bash
uvicorn main:app --reload --port 8000
```
- Ngrok 설정: `ngrok http 8000` 실행 후 발급된 주소를 카카오 i 오픈빌더 서버 설정에 등록 후 테스트.

---

## 📝 현재 진행 상황 및 과제
- 기존 연결된 FastAPI 및 Google Sheets 코어 코드가 존재하나, **완전히 새로워진 기획(설계.md 참조)을 반영하기 위해 부분 리팩토링(STEP 1)**을 진행해야 합니다.
- **주요 수정 대상**:
  - `Member_Master`의 휴무 잔여량 로직(주간 1.0, 월휴 1)에 맞게 초기 데이터 주입 수정.
  - `Daily_Log` 트랜잭션 스키마 확장 (승인여부, 사진누적 컬럼 등 대응).
  - Webhook을 통해 들어온 UserKey로 유저 검증 로직 도입.

_자세한 핵심 운영 룰과 DB 스키마는 동봉된 `설계.md`를 참고하세요._
