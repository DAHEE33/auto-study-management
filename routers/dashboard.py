from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from integrations.google_sheets import sheets_client
from datetime import datetime, timedelta
from services.leave_reset_service import leave_reset_service
import os

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

current_dir = os.path.dirname(os.path.realpath(__file__))
templates_dir = os.path.join(os.path.dirname(current_dir), "templates")
templates = Jinja2Templates(directory=templates_dir)

@router.get("", response_class=HTMLResponse)
async def view_dashboard(request: Request, user: str = Query(None), view: str = Query("weekly")):
    """
    개인화된 스터디 현황과 순위, 그리고 그룹 전체 출석 매트릭스를 제공합니다.
    개인화된 스터디 현황과 순위, 그리고 그룹 전체 출석 매트릭스를 제공합니다.
    view 파라미터가 'monthly' 이면 월간, 그 외에는 주간(weekly)으로 동작합니다.
    """
    try:
        leave_reset_service.run_if_needed()
    except Exception as e:
        print(f"⚠️ 휴무 자동 갱신 체크 실패(dashboard): {e}")

    members = sheets_client.get_sheet_records("Member_Master")
    logs = sheets_client.get_sheet_records("Daily_Log")
    
    active_members = [m for m in members if str(m.get("상태", "")) == "활동"]
    ordered_nicks = [str(m.get("닉네임", "")).strip() for m in active_members if str(m.get("닉네임", "")).strip()]
    
    # 시간 추출 헬퍼 (예: 1시간 30분 -> 90 반환)
    def parse_duration_to_min(dur_str):
        if not dur_str: return 0
        try:
            h = 0
            m = 0
            if "시간" in dur_str:
                parts = dur_str.split("시간")
                h = int(parts[0].strip())
                if "분" in parts[1]:
                    m = int(parts[1].replace("분", "").strip())
            elif "분" in dur_str:
                m = int(dur_str.replace("분", "").strip())
            return h * 60 + m
        except:
            return 0
            
    # 오늘 기준 주간/월간 날짜 범위 세팅
    today = datetime.now()
    if view == "monthly":
        first_day = today.replace(day=1)
        days_in_month = (today - first_day).days + 1
        date_objs = [first_day + timedelta(days=i) for i in range(days_in_month)]
    else:
        weekday = today.weekday()
        monday = today - timedelta(days=weekday)
        date_objs = [monday + timedelta(days=i) for i in range(5)]

    date_strs = [d.strftime("%Y-%m-%d") for d in date_objs]
    display_dates = [d.strftime("%m/%d(%a)") for d in date_objs]
    
    # 1. 누적 시간 직접 집계 (DB 최종누적 무시)
    acc_map = {m.get("닉네임", ""): 0 for m in active_members}
    
    for log in logs:
        d_str = str(log.get("날짜", ""))
        n_str = str(log.get("닉네임", ""))
        dur = parse_duration_to_min(str(log.get("당일시간", "")))
        
        # view 범위에 맞는 로그만 합산
        if d_str in date_strs and n_str in acc_map:
            acc_map[n_str] += dur
            
    # 2. 리더보드 구성 (동적 합산 기준 정렬)
    ranked_nicks = sorted(acc_map.keys(), key=lambda k: acc_map[k], reverse=True)
    
    leaderboard = []
    user_data = None
    user_rank = "-"
    user_acc_min = 0
    
    for idx, nick in enumerate(ranked_nicks):
        rank = idx + 1
        acc_min = acc_map[nick]
        
        if rank <= 3 and acc_min > 0:
            leaderboard.append({
                "rank": rank,
                "nickname": nick,
                "fmt_time": f"{acc_min//60}h {acc_min%60}m"
            })
            
        if nick == user:
            user_rank = rank
            user_acc_min = acc_min
            # Member 데이터 매칭
            for m in active_members:
                if m.get("닉네임") == nick:
                    user_data = m
                    break
                    
    total_members = len(active_members)

    # 3. 내 데이터 세팅
    if user_data:
        my_stats = {
            "is_valid": True,
            "nickname": user,
            "rank": user_rank,
            "total": total_members,
            "weekly_leave": user_data.get("주간휴무", "0"),
            "monthly_leave": user_data.get("남은월휴", "0"),
            "deposit": user_data.get("예치금", "0"),
            "acc_time": f"{user_acc_min//60}시간 {user_acc_min%60}분"
        }
    else:
        my_stats = {
            "is_valid": False,
            "nickname": user if user else "로그인 필요",
            "rank": "-",
            "total": total_members,
            "weekly_leave": "-",
            "monthly_leave": "-",
            "deposit": "-",
            "acc_time": "0시간 0분"
        }
    
    # 4. 매트릭스 피벗
    matrix = {}
    for d in date_strs:
        matrix[d] = {n: {"status": "-", "type": "-", "penalty": 0, "dur_str": "", "tooltip": "기록 없음"} for n in ordered_nicks}
        
    for log in logs:
        d = str(log.get("날짜", ""))
        n = str(log.get("닉네임", ""))
        
        if d in matrix and n in matrix[d]:
            status = str(log.get("판정", ""))
            ltype = str(log.get("유형", ""))
            dur = str(log.get("당일시간", ""))
            
            try:
                pen = int(str(log.get("벌금액", "0")).replace(",", ""))
            except ValueError:
                pen = 0
            
            # 툴팁
            if pen < 0:
                tooltip = f"{status}({pen}원) | {dur}"
            elif ltype in ["주휴", "월휴", "특휴"]:
                tooltip = f"[{ltype}]"
            elif ltype == "반휴":
                tooltip = f"반휴({pen}원) | {dur}"
            else:
                tooltip = f"PASS | {dur}"
                
            matrix[d][n] = {
                "status": status,
                "type": ltype,
                "penalty": pen,
                "dur_str": dur,
                "tooltip": tooltip
            }

    is_weekly = view != "monthly"

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "my_stats": my_stats,
            "leaderboard": leaderboard,
            "date_strs": date_strs,
            "display_dates": display_dates,
            "nicknames": ordered_nicks,
            "matrix": matrix,
            "is_weekly": is_weekly,
        }
    )
