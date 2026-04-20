# 🚀 Project: Study-Sync (Auto-Settlement Engine)

> **"관리 리소스 Zero를 지향하는 스터디 운영 자동화 솔루션"**
> 본 프로젝트는 카카오톡 챗봇, OCR, Google Sheets API를 결합하여 출석 인증, 벌금 정산, 스토리지 관리를 자동화하는 **Serverless 정산 시스템**입니다.

## 🛠 Tech Stack
- **Language**: Python 3.11
- **Framework**: FastAPI (Asynchronous)
- **Cloud & DB**: Google Cloud Vision API (OCR), Google Sheets API (DB)
- **Infrastructure**: Oracle Cloud (Ubuntu), Nginx, Systemd, 카카오 i 오픈빌더 스킬
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

### 2. 로컬 테스트 (Local)
```bash
uvicorn main:app --reload --port 8000
```
- Ngrok 설정: `ngrok http 8000` 실행 후 생성된 주소를 오픈빌더에 등록하여 임시 테스트 진행.

### 3. 클라우드 배포 (Production - Oracle Cloud)
- **인스턴스**: Oracle Cloud Free Tier (Ubuntu)
- **무중단 서비스 (Systemd)**: `studysync.service` 데몬을 생성하여 FastAPI(Uvicorn) 백그라운드 구동
- **웹 서버 및 SSL**: Nginx 리버스 프록시 적용 후 `Certbot`으로 카카오 연동 필수 조건인 HTTPS 전면 적용
- 카카오 오픈빌더 스킬에 발급받은 최종 도메인 주소 등록 완료

---

## 📝 현재 진행 상황 및 성과 (Phase 1 ~ Phase 3 완료)
기존 아키텍처를 `Plan.md`의 예외 인정 룰과 시간 엄수 룰에 맞게 완벽하게 고도화했습니다.

### ✨ **핵심 기능 구현 완료**
1. **DB 및 코어 엔진**: 구글 시트 초기화 로직 및 엄격한 시간 판독 엔진(`check_in_engine.py`) 적용 (낮 12:00 ~ 16:59 블랙아웃 차단 및 익일 01:00 ~ 02:00 결석/예외 판정 고도화 완료).
2. **카카오 챗봇 연동 및 Stateful 메모리**: UserKey를 기반으로 10분간 유저의 액션을 기억하며(반휴/특휴 분기), `"등록 닉네임"` 발화 시 카카오 기기를 즉시 자동 매핑, `"목표변경 3시간"` 등의 자동 수정 기능을 지원.
3. **고도화된 파싱 및 조작 검증 (OCR)**: 당일시간과 누적시간은 물론 **사진에 적힌 인식 시점과 누적 시간 오차(60분)** 를 판독하여 (과거 사진, 허위 누적 시간) 제출을 사전에 차단하고 -5,000 벌금 부여.
4. **결산 및 운영 자동화 (배치 잡 최적화)**:
   - `jobs/daily_absence.py`: 매일 정오, 미인증자를 색출해 결석 벌금(-2000원) 자동 작성.
   - `jobs/weekly_settlement.py`: 매주 토요일 정오, 주간 결산 및 상금 배분 **안내 메시지를 생성하여 Admin_Config 시트에 자동 저장** (비즈니스 채널 없이도 방장이 손쉽게 복붙 가능).
   - [NEW] **카카오 CDN 활용 스토리지 최적화**: Гугл 드라이브 다운로드/업로드를 전면 폐기하고 카카오 원본 링크를 활용하여 속도를 높이고 스토리지 비용을 0원으로 만듦 (따라서 기존 이미지 삭제 배치는 불필요).
5. **휴무일(자율참여) 완전 차단 로직**: 휴무일로 지정된 날짜에는 인증 판독이나 휴가 처리를 일절 받지 않도록 최상단에서 차단하여 방장과 챗봇의 혼선을 완벽 차단.
