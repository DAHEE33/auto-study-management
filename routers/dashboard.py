from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from integrations.google_sheets import sheets_client

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

@router.get("", response_class=HTMLResponse)
async def view_dashboard():
    """
    스터디 현황을 보여주는 아름다운 실시간 대시보드 (Webview 연동용)
    """
    members = sheets_client.get_sheet_records("Member_Master")
    logs = sheets_client.get_sheet_records("Daily_Log")
    
    # 통계 계산
    total_penalty = 0
    member_stats = {m.get("닉네임", "Unknown"): {"penalty": 0, "logs": 0} for m in members}
    
    for log in logs:
        nick = str(log.get("닉네임", ""))
        penalty_str = str(log.get("벌금", "0")).replace(",", "")
        try:
            fine = int(penalty_str) if penalty_str and penalty_str != "-" else 0
        except ValueError:
            fine = 0
            
        total_penalty += abs(fine)
        
        if nick in member_stats:
            member_stats[nick]["penalty"] += abs(fine)
            member_stats[nick]["logs"] += 1
            
    # 정산 N빵 반영: '벌금 0원'인 멤버 수로만 N빵 배분 (100원 단위 절삭)
    zero_penalty_count = sum(1 for stat in member_stats.values() if stat["penalty"] == 0)
    split_bonus = 0
    if zero_penalty_count > 0:
        raw_bonus = total_penalty // zero_penalty_count
        split_bonus = (raw_bonus // 100) * 100  # 100원 단위 절삭

    # HTML 렌더링 카드 생성
    cards_html = ""
    for nick, stat in member_stats.items():
        # 벌금이 0원인 사람만 보너스를 받음
        bonus_display = f"<p class='bonus'>예상 정산(보너스): +{split_bonus}원</p>" if stat['penalty'] == 0 else "<p class='loss'>예상 정산: 보너스 대상 제외</p>"
        
        cards_html += f"""
        <div class="card">
            <h3>{nick}</h3>
            <p>인증 횟수: <b>{stat['logs']}회</b></p>
            <p>누적 벌금: <b>{stat['penalty']}원</b></p>
            {bonus_display}
        </div>
        """

    html_content = f"""
    <!DOCTYPE html>
    <html lang="ko">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Study-Sync 통계 대시보드</title>
        <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;500;700&display=swap" rel="stylesheet">
        <style>
            :root {{
                --bg-color: #0f172a;
                --card-bg: rgba(30, 41, 59, 0.7);
                --text-main: #f8fafc;
                --text-muted: #94a3b8;
                --accent: #3b82f6;
                --accent-glow: rgba(59, 130, 246, 0.5);
            }}
            body {{
                font-family: 'Outfit', sans-serif;
                background-color: var(--bg-color);
                color: var(--text-main);
                margin: 0;
                padding: 40px 20px;
                display: flex;
                flex-direction: column;
                align-items: center;
            }}
            .container {{
                max-width: 800px;
                width: 100%;
            }}
            .header {{
                text-align: center;
                margin-bottom: 40px;
            }}
            h1 {{
                font-size: 2.5rem;
                margin: 0 0 10px 0;
                background: linear-gradient(to right, #60a5fa, #a78bfa);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
            }}
            .summary-box {{
                background: linear-gradient(145deg, var(--card-bg), rgba(15, 23, 42, 0.9));
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 20px;
                padding: 30px;
                text-align: center;
                box-shadow: 0 10px 30px rgba(0,0,0,0.5), inset 0 1px 0 rgba(255,255,255,0.1);
                backdrop-filter: blur(10px);
                margin-bottom: 40px;
            }}
            .summary-box h2 {{
                margin: 0;
                font-weight: 300;
                color: var(--text-muted);
            }}
            .total-penalty {{
                font-size: 3.5rem;
                font-weight: 700;
                color: #ef4444;
                margin: 10px 0 0 0;
                text-shadow: 0 0 20px rgba(239, 68, 68, 0.4);
            }}
            .grid {{
                display: grid;
                grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
                gap: 20px;
            }}
            .card {{
                background: var(--card-bg);
                border: 1px solid rgba(255, 255, 255, 0.05);
                border-radius: 15px;
                padding: 20px;
                transition: transform 0.3s ease, box-shadow 0.3s ease;
                backdrop-filter: blur(5px);
            }}
            .card:hover {{
                transform: translateY(-5px);
                box-shadow: 0 10px 20px var(--accent-glow);
                border-color: var(--accent);
            }}
            .card h3 {{
                margin-top: 0;
                border-bottom: 1px solid rgba(255,255,255,0.1);
                padding-bottom: 10px;
            }}
            .bonus {{
                color: #10b981;
                font-weight: bold;
                background: rgba(16, 185, 129, 0.1);
                padding: 8px;
                border-radius: 8px;
                display: inline-block;
                margin-top: 10px;
            }}
            .loss {{
                color: #ef4444;
                font-weight: bold;
                background: rgba(239, 68, 68, 0.1);
                padding: 8px;
                border-radius: 8px;
                display: inline-block;
                margin-top: 10px;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Study-Sync Dashboard</h1>
                <p>실시간 스터디 정산 현황</p>
            </div>
            
            <div class="summary-box">
                <h2>현재 이번 주 누적 벌금 전체</h2>
                <div class="total-penalty">{total_penalty:,} 원</div>
            </div>
            
            <div class="grid">
                {cards_html}
            </div>
        </div>
    </body>
    </html>
    """
    return html_content
