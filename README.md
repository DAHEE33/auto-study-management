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

## 📝 현재 진행 상황 및 성과 (Phase 1 & Phase 2 완료)
기존 아키텍처를 `설계.md`의 최신 운영 룰에 맞게 성공적으로 리팩토링 및 고도화했습니다.

### ✨ **핵심 기능 구현 완료**
1. **DB 및 코어 엔진**: 구글 시트 초기화 자동 로직 및 `check_in_engine.py` (05시 기준 휴무/결석 판별기) 분리 완료.
2. **카카오 챗봇 연동**: `webhook.py`에서 UserKey를 기반으로 유저를 식별하고, 반휴/특휴/주휴/일반 인증 분기를 완벽히 분리.
3. **고도화된 파싱 (OCR)**: 구루미 타이머의 `X시간 Y분` 형식을 읽어 당일시간과 누적시간을 추출하도록 `ocr_service.py` 정규식 적용.
4. **결산 및 청소 자동화 (배치 잡)**:
   - `jobs/daily_absence.py`: 매일 정오, 미인증자를 색출해 결석 벌금(-2000원) 자동 작성.
   - `jobs/weekly_settlement.py`: 매주 토요일, 1주간의 벌금을 산정해 1/n로 배분하는 정산 리포트를 `Admin_Config` 공지칸에 자동 생성.
   - `jobs/cleanup_images.py`: 14일 초과 인증 이미지를 찾아 구글 드라이브에서 휴지통 비우기.
