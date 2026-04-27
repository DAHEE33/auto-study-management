from fastapi import FastAPI
from contextlib import asynccontextmanager
from routers import webhook, dashboard
from integrations.google_sheets import sheets_client
from services.leave_reset_service import leave_reset_service

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 Study-Sync 서버 시작: 구글 시트 초기화(setup_initial_data) 확인 중...")
    try:
        sheets_client.setup_initial_data()
        leave_reset_service.run_if_needed()
    except Exception as e:
        print(f"⚠️ 구글 시트 세팅 중 오류 발생: {e}")
    yield
    print("🛑 Study-Sync 서버가 종료됩니다.")

app = FastAPI(title="Study-Sync Auto-Settlement API", version="1.0.0", lifespan=lifespan)

app.include_router(webhook.router, prefix="/api/v1")
app.include_router(dashboard.router)

@app.get("/")
def read_root():
    return {"message": "Welcome to Study-Sync API", "status": "ok"}
