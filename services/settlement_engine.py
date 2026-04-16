from typing import List, Dict

class SettlementEngine:
    def __init__(self):
        pass
        
    def calculate_penalty(self, target_minutes: int, auth_minutes: int, is_late_submit: bool, is_fake_time: bool, is_fake_date: bool, is_absent: bool = False) -> int:
        """
        벌금 산정 로직
        - 결석(아예 전송 안한 경우 거나 01~02시 목표 미달 시): -2000
        - 시간 미달 (1시간 이상 인증): -500
        - 시간 미달 (1시간 미만 인증): -1000
        - 허위 인증 (날짜/누적 오류): -1000 (날짜), -5000 (누적시간)
        """
        if is_absent:
            return -2000
            
        if is_fake_time:
            return -5000
            
        if is_fake_date:
            return -1000
            
        if auth_minutes < target_minutes:
            if auth_minutes >= 60:
                return -500
            else:
                return -1000
                
        return 0

    def generate_weekly_report(self, start_date: str, end_date: str, daily_logs: List[Dict], master_members: List[Dict], admin_notice: str = "") -> str:
        """
        주간 결산 템플릿 생성기. (매주 토요일 정오 호출용)
        - 배분: (총 벌금) / (벌금 0원 성실 멤버 수)
        - 상금 대상, 예치금 경고 알림 등 포괄
        """
        total_penalty_accumulated = 0
        sincere_members = []
        member_penalties = {m['닉네임']: 0 for m in master_members if str(m['상태']) == '활동'}
        
        # 1. 일주일치 로그에서 벌금 집계 (실제로는 date_string 필터링 필요)
        for log in daily_logs:
            try:
                penalty_val = int(str(log.get('벌금액', '0')).replace(',', ''))
            except:
                penalty_val = 0
                
            nick = log.get('닉네임')
            if nick in member_penalties:
                if penalty_val < 0:
                    member_penalties[nick] += abs(penalty_val)
                    total_penalty_accumulated += abs(penalty_val)
        
        # 2. 성실 멤버 산정
        for nick, pen_amt in member_penalties.items():
            if pen_amt == 0:
                sincere_members.append(nick)
                
        # 3. 1/n 보상금액
        reward_per_user = 0
        if total_penalty_accumulated > 0 and len(sincere_members) > 0:
            reward_per_user = total_penalty_accumulated // len(sincere_members)

        # 4. 결산 텍스트 조립
        report = f"[Study-Sync 주간 결산] 📅 {start_date} ~ {end_date}\n\n"
        report += "이번 주도 고생 많으셨습니다!\n정산 결과를 발표합니다.\n\n"
        report += f"💰 벌금 합계: {total_penalty_accumulated:,}원\n"
        report += f"💰 1/n 배분액: +{reward_per_user:,}원 (성실멤버 {len(sincere_members)}명)\n\n"
        
        report += "📢 관리자 공지사항:\n"
        report += "-" * 25 + "\n"
        report += f"{admin_notice if admin_notice else '특별한 공지사항이 없습니다.'}\n"
        report += "-" * 25 + "\n"
        
        return report

settlement_engine = SettlementEngine()
