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

**2026-08-11: 저장 위치가 참가자별 세션 폴더로 바뀌었다**(core/result_paths.py). 파일명을
직접 짓지 않고 launcher.py가 정해준 session_dir 안에 `대화.txt`로 쓴다 — 같은 세션의 퀴즈
JSON과 반드시 같은 폴더에 들어가게 하기 위함. 동시에 **이름을 몰라도 저장한다**: 예전에는
user_name이 없으면 그대로 return해서, 참가자가 끝내 이름을 말하지 않으면 그 사람 대화록이
통째로 사라졌다(N=30 실험에서는 그대로 데이터 손실).
"""
from __future__ import annotations
import os
from datetime import datetime


def save_conversation_log(user_name: str | None, conversation_log: str, session_dir: str) -> None:
    """session_dir(core/result_paths.make_session_dir()) 안에 대화.txt를 쓴다.

    user_name은 이제 저장 여부를 좌우하지 않고 머리말에만 쓰인다 — 폴더는 진행자가 지정한
    참가자ID로 이미 정해져 있으므로, 이름을 몰라도 어느 참가자 것인지 알 수 있다.
    """
    try:
        now = datetime.now()
        display_name = user_name if user_name and user_name != "Unknown" else "이름 미확인"
        os.makedirs(session_dir, exist_ok=True)

        chat_filename = os.path.join(session_dir, "대화.txt")
        formatted_log = ""
        for line in conversation_log.split('\n'):
            parts = line.split(" | Moti: ")
            if len(parts) == 2:
                formatted_log += f"👤 사용자: {parts[0].replace('User: ', '').strip()}\n"
                formatted_log += f"🤖 모티: {parts[1].strip()}\n\n"
            else:
                formatted_log += line + "\n"

        with open(chat_filename, "w", encoding="utf-8") as f:
            f.write(f"--- {display_name}님과의 전체 대화 기록 ---\n")
            f.write(f"일시: {now.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write(formatted_log)
        print(f"📄 전체 대화문 저장 완료: {chat_filename}")

    except Exception as e:
        print(f"❌ 대화록 저장 중 오류 발생: {e}")
