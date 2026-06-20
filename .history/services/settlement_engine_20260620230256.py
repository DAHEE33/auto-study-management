from datetime import datetime, timedelta
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
        - 배분: (총 벌금) / (벌금 0원 + 주 4일 이상 참여 멤버 수)
        - 벌금 대상, 예치금 소진 안내를 함께 출력
        """
        def _to_int(raw, default=0):
            txt = str(raw).replace(",", "").strip()
            if txt.startswith("-"):
                return int(txt) if txt[1:].isdigit() else default
            return int(txt) if txt.isdigit() else default

        member_penalties = {}
        member_participation_days = {}
        for member in master_members:
            nick = str(member.get("닉네임", "")).strip()
            status = str(member.get("상태", "")).strip()
            if not nick or status != "활동":
                continue
            member_penalties[nick] = 0
            member_participation_days[nick] = set()

        participation_types = {"일반", "반휴", "주휴", "월휴", "특휴"}

        for log in daily_logs:
            nick = str(log.get("닉네임", "")).strip()
            if nick not in member_penalties:
                continue

            date_str = str(log.get("날짜", "")).strip()
            log_type = str(log.get("유형", "")).strip()
            is_participation = log_type in participation_types
            if date_str and is_participation:
                member_participation_days[nick].add(date_str)

            penalty_val = _to_int(log.get("벌금액", "0"), default=0)
            if penalty_val < 0:
                member_penalties[nick] += abs(penalty_val)

        total_penalty_accumulated = sum(member_penalties.values())

        reward_targets = []
        for nick, penalty_amount in member_penalties.items():
            participation_days = len(member_participation_days.get(nick, set()))
            if penalty_amount == 0 and participation_days >= 4:
                reward_targets.append(nick)

        reward_per_user = 0
        if total_penalty_accumulated > 0 and reward_targets:
            reward_per_user = total_penalty_accumulated // len(reward_targets)

        penalty_targets = []
        for nick, amount in member_penalties.items():
            if amount > 0:
                penalty_targets.append(f"{nick}(-{amount:,})")

        depleted_targets = []
        for member in master_members:
            nick = str(member.get("닉네임", "")).strip()
            if not nick:
                continue
            status = str(member.get("상태", "")).strip()
            deposit = _to_int(member.get("예치금", "0"), default=0)
            if status == "예치금 소진" or deposit <= 0:
                depletion_date_str = str(member.get("예치금소진일자", "")).strip()
                deadline_text = "-"
                if depletion_date_str and depletion_date_str != "-":
                    try:
                        depletion_date = datetime.strptime(depletion_date_str, "%Y-%m-%d").date()
                        deadline_text = (depletion_date + timedelta(days=3)).strftime("%Y-%m-%d")
                    except ValueError:
                        deadline_text = "-"
                depleted_targets.append(f"{nick}({deposit:,}, 마감:{deadline_text})")

        report_lines = [
            f"[주간 정산 안내] 📅 {start_date} ~ {end_date}",
            "",
            "1) 이번주 벌금 요약",
            f"- 이번 주 벌금: {total_penalty_accumulated:,}원 / 벌금 없는 사람들 {len(reward_targets)}명 (주 4일 이상 참여 기준)",
            f"- 1/n 배분액: +{reward_per_user:,}원",
            f"- 벌금 대상자: {', '.join(penalty_targets) if penalty_targets else '없음'}",
            "- 상금은 상황에 따라 변경될 수도 있습니다.",
            "",
            "2) 예치금 추가 요청",
            f"- 대상자(예치금 소진): {', '.join(depleted_targets) if depleted_targets else '없음'}",
            "- 위 대상자는 각자 마감일 자정 전까지 추가 예치금 입금 부탁드립니다.(미입금 시 스터디 종료)",
        ]

        if admin_notice:
            report_lines.extend(["", f"📢 관리자 공지: {admin_notice}"])

        return "\n".join(report_lines)

settlement_engine = SettlementEngine()
