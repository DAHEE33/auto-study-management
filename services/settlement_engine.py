from typing import List, Dict

class SettlementEngine:
    def __init__(self):
        pass
        
    def calculate_penalty(self, target_minutes: int, auth_minutes: int, is_late_submit: bool, is_fake_time: bool, is_fake_date: bool) -> int:
        """
        벌금 산정 로직
        - 결석(아예 전송 안한 경우): -2000 (이 함수 밖에서 일괄 배치 작업으로 산정)
        - 시간 미달 (1시간 이상 인증): -500
        - 시간 미달 (1시간 미만 인증): -1000
        - 허위 인증 (날짜 등 불일치): -1000
        - 거짓 인증 (누적시간 데이터 조작 등): -5000
        """
        if is_fake_time:
            return -5000
            
        if is_fake_date:
            return -1000
            
        if auth_minutes < target_minutes:
            if auth_minutes >= 60:
                return -500
            else:
                return -1000
                
        # (규칙) 01:00 넘겨서 공부가 끝났더라도 목표시간을 달성하면 인정이지만,
        # 01:00 이후에 보내는 "지각 발송" 건에 대해서는 추가 심사를 해야함.
        # 일단 로직의 순수 벌금 금액은 0원을 반환 (추후 외부에서 01:00 타임스탬프와 비교).
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
