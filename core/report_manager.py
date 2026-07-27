"""세션 종료 시 '마음 처방전'(상담 결과지) + 전체 대화록을 파일로 저장.

v2 core/report_manager.py 포팅. v2는 학년/전공/RC/MBTI 같은 고정 슬롯(user_info dict)을
입력으로 받았는데, v3는 그런 슬롯이 없으므로(§04 설계 결정) profile_manager.load_profile_for_chat()가
만드는 자유형 facts_summary 문자열을 그대로 프롬프트에 박아 넣는다.

conversation_log는 "User: {발화} | Moti: {응답}" 형식 줄바꿈 로그를 기대한다(v2와 동일 계약) —
launcher.py가 매 턴마다 이 형식으로 session_history를 쌓아서 그대로 넘긴다.
"""
import os
from datetime import datetime

import google.generativeai as genai

from core.utils import _extract_text

MODEL_NAME = os.getenv("MODEL_NAME", "gemini-3.1-flash-lite")


def _format_vitals(vitals_data: dict | None) -> str:
    if not vitals_data:
        return "- 신체 활력 데이터: 없음 (측정 불가)"
    return f"""
- 평균 심박수: {vitals_data.get('avg_hr', '알 수 없음')}
- 최고 심박수: {vitals_data.get('max_hr', '알 수 없음')}
- 스트레스 지수: {vitals_data.get('stress', '알 수 없음')}
- 기분 요약: {vitals_data.get('mood', '알 수 없음')}
"""


def generate_and_save_reports(user_name: str, conversation_log: str, facts_summary: str | None,
                               vitals_data: dict | None = None) -> None:
    """user_name이 없거나 "Unknown"이면 아무것도 하지 않는다(v2와 동일한 방어 조건)."""
    if not user_name or user_name == "Unknown":
        return

    try:
        today_str = datetime.now().strftime("%Y-%m-%d")
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        result_dir = os.path.join(base_dir, "user_result")
        os.makedirs(result_dir, exist_ok=True)

        # 1. 대화록 저장
        chat_filename = os.path.join(result_dir, f"{today_str}_{user_name}_대화.txt")
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
            f.write(f"일시: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write(formatted_log)
        print(f"📄 전체 대화문 저장 완료: {chat_filename}")

        vitals_text = _format_vitals(vitals_data)
        facts_text = facts_summary or "- 대화 중 자연스럽게 알아낸 정보 없음"

        # 2. 결과지 생성
        print(f"⏳ {user_name}님의 상담 결과지 자동 생성을 시작합니다...")
        report_prompt = f"""
당신은 한동대학교 학우들의 지친 마음을 달래주는 따뜻한 공감 로봇 '모티(Moti)'의 전문 심리 분석 AI입니다.
아래의 [내담자 정보], [신체 활력 데이터], [전체 대화 내용]을 바탕으로, 사용자가 읽고 큰 위로와 재미를 느낄 수 있는 '모티의 마음 처방전'을 작성해 주세요.

[내담자 정보 — 대화 중 자연스럽게 알게 된 것들]
이름: {user_name}
{facts_text}

[신체 활력 데이터]
{vitals_text}

[전체 대화 내용]
{formatted_log}

[작성 가이드라인 - 아래 양식과 이모지를 반드시 지켜서 작성하세요]

## 💌 모티가 발급한 [ {user_name} ]님만의 마음 처방전 💌

**"오늘 하루도 정말 고생 많으셨어요. 모티가 당신의 내일을 응원합니다!"**

---

### 📋 1. 오늘의 내담자 프로필
* **이름:** {user_name} 학우님
* **오늘의 특징:** [위 내담자 정보에서 눈에 띄는 것(전공/MBTI/취미 등 아는 게 있다면)을 한 줄로 짚어주세요. 모르면 이 항목은 자연스럽게 생략하세요.]

### 🩺 2. 모티의 마음 진단서
* **오늘의 마음 온도:** [대화 내용을 바탕으로 오늘의 기분을 비유적으로 표현하세요. 예: 먹구름 낀 뒤 점차 맑아짐 ⛅]
* **증상 요약:** [사용자가 겪은 힘든 일이나 고민을 2~3줄로 따뜻하게 요약하세요.]
* **모티의 공감 시선:** [왜 사용자가 그런 감정을 느끼는 것이 당연하고 자연스러운지, 그들의 노력을 칭찬하고 깊이 공감해주는 내용을 적어주세요.]

### 💊 3. 맞춤형 힐링 처방
* **행동 처방전:** [대화에서 제시했던 솔루션이나, 사용자가 당장 오늘 밤이나 내일 해보면 좋을 작고 확실한 행복(소확행) 행동을 추천해주세요. 한동대 캠퍼스(히즈빈스, 평봉필드 등)나 야식 메뉴 등을 언급하면 좋습니다.]
* **추천 힐링 BGM:** [사용자의 현재 기분과 상황에 딱 맞는 위로가 되거나 신나는 노래 1곡을 추천하고, 추천 이유를 덧붙여주세요.]

### 🫀 4. 신체 활력 & 스트레스 분석
{vitals_text}
* **건강 코멘트:** [제공된 데이터를 바탕으로 가볍게 건강 상태를 짚어주고, 휴식을 권장하는 따뜻한 멘트를 남겨주세요. 데이터가 없다면 이 항목은 생략하세요.]

---

### 💬 모티의 비밀 편지
"[이곳에는 모티의 1인칭 시점으로, 내담자에게 직접 말하듯 반말과 존댓말을 적절히 섞은 친근하고 다정한 응원의 편지를 3~4문장으로 작성해주세요. '언제든 또 찾아와'라는 뉘앙스를 꼭 넣어주세요.]"
"""
        model = genai.GenerativeModel(MODEL_NAME)
        response = model.generate_content(report_prompt)
        report_text = _extract_text(response)

        report_filename = os.path.join(result_dir, f"{today_str}_{user_name}_결과지.txt")
        with open(report_filename, "w", encoding="utf-8") as f:
            f.write(report_text)
        print(f"📄 상담 결과지 저장 완료: {report_filename}")

    except Exception as e:
        print(f"❌ 보고서 자동 저장 중 오류 발생: {e}")
