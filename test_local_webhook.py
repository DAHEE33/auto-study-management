import httpx
import asyncio
import json

async def simulate_kakao_request(utterance: str, image_url: str = ""):
    url = "http://127.0.0.1:8000/api/v1/webhook"
    
    # 카카오 i 챗봇이 서버로 쏴주는 JSON 형태 (가짜 데이터)
    payload = {
        "userRequest": {
            "timezone": "Asia/Seoul",
            "user": {
                "id": "UK123"  # 시트 내 dev_user의 UserKey와 동일함
            },
            "utterance": utterance,
            "block": {
                "name": "테스트 블록"
            }
        },
        "action": {
            "detailParams": {}
        }
    }

    # 이미지 URL이 있다면 action param에 추가
    if image_url:
        payload["action"]["detailParams"] = {
            "secureimage": {
                "origin": image_url
            }
        }

    print(f"\n📨 [TEST] 챗봇에 발화 전송: '{utterance}'")
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, json=payload, timeout=10.0)
            response.raise_for_status()
            res_json = response.json()
            
            # 카카오의 SimpleText 응답 부분 파싱해서 출력
            bot_reply = res_json.get("template", {}).get("outputs", [{}])[0].get("simpleText", {}).get("text", "응답 없음")
            print("🤖 [BOT 응답]:\n" + "-" * 40)
            print(bot_reply)
            print("-" * 40)
            
        except Exception as e:
            print(f"❌ 요청 실패: {e}")

async def main():
    print("🚀 로컬 카카오 웹훅 시뮬레이션 테스트를 시작합니다.")
    print("원하시는 액션 번호를 선택해주세요.\n")
    print("1: [내 현황 확인] 테스트 (단순 상태 조회)")
    print("2: [주휴 사용] 테스트 (DB 차감 및 PASS 기록 연동)")
    print("3: [방어막 확인] 주휴 연속 사용해보기 (차단 되는지 확인)")
    
    choice = input("\n숫자 입력: ")
    
    if choice == "1":
        await simulate_kakao_request("내 현황 확인")
    elif choice == "2":
        await simulate_kakao_request("주휴 사용")
    elif choice == "3":
        for i in range(2):
            print(f"\n>> {i+1}번째 주휴 사용 시도중...")
            await simulate_kakao_request("주휴 사용")
            await asyncio.sleep(1)
    else:
        print("잘못된 입력입니다.")

if __name__ == "__main__":
    asyncio.run(main())
