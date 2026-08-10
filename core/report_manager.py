"""세션 종료 시 전체 대화록을 파일로 저장.

**2026-08-10: '마음 처방전'(LLM 상담 결과지) 생성 기능을 제거했다.** 세션이 끝날 때마다
전체 대화록을 통째로 프롬프트에 넣고 긴 편지를 생성하는 배치 호출이었는데, API 지출
상한에 걸린 상황에서 실험 데이터로 쓰이지도 않는 비용이라 사용자가 제거를 결정했다.
연구에 실제로 쓰이는 건 (a) 여기서 저장하는 대화록 원문과 (b) core/quiz_export.py의
문항별 JSON이고, 둘 다 API를 전혀 쓰지 않는다.

되살려야 하면 git 이력에서 프롬프트를 그대로 복원할 수 있다
(`git log -p -- core/report_manager.py`, 2026-08-10 이전 버전).

conversation_log는 "User: {발화} | Moti: {응답}" 형식 줄바꿈 로그를 기대한다(v2와 동일 계약) —
launcher.py가 매 턴마다 이 형식으로 session_history를 쌓아서 그대로 넘긴다.
"""
import os
from datetime import datetime


def save_conversation_log(user_name: str, conversation_log: str) -> None:
    """user_name이 없거나 "Unknown"이면 아무것도 하지 않는다(v2와 동일한 방어 조건)."""
    if not user_name or user_name == "Unknown":
        return

    try:
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        # 2026-07-31 코드 리뷰로 발견: 날짜만으로 파일명을 지으면 같은 참가자를 같은 날
        # 다시 진행해야 하는 경우(기술적 문제로 재실험 등) 이전 시도의 대화록이 조용히
        # 덮어써진다 — core/quiz_export.py가 22단계에서 이미 겪고 고친 문제와 동일해서,
        # 여기도 시각(HHMMSS)을 파일명에 포함시켜 같은 방식으로 맞춘다.
        time_str = now.strftime("%H%M%S")
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        result_dir = os.path.join(base_dir, "user_result")
        os.makedirs(result_dir, exist_ok=True)

        chat_filename = os.path.join(result_dir, f"{today_str}_{time_str}_{user_name}_대화.txt")
        formatted_log = ""
        for line in conversation_log.split('\n'):
            parts = line.split(" | Moti: ")
            if len(parts) == 2:
                formatted_log += f"👤 사용자: {parts[0].replace('User: ', '').strip()}\n"
                formatted_log += f"🤖 모티: {parts[1].strip()}\n\n"
            else:
                formatted_log += line + "\n"

        with open(chat_filename, "w", encoding="utf-8") as f:
            f.write(f"--- {user_name}님과의 전체 대화 기록 ---\n")
            f.write(f"일시: {now.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write(formatted_log)
        print(f"📄 전체 대화문 저장 완료: {chat_filename}")

    except Exception as e:
        print(f"❌ 대화록 저장 중 오류 발생: {e}")
