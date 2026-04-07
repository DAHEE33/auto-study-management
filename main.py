from fastapi import FastAPI
from routers import webhook, dashboard

app = FastAPI(title="Study-Sync Auto-Settlement API", version="1.0.0")

app.include_router(webhook.router, prefix="/api/v1")
app.include_router(dashboard.router)

@app.get("/")
def read_root():
    return {"message": "Welcome to Study-Sync API", "status": "ok"}
