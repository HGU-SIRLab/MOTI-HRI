"""set_emotion — docs/architecture.md §06. v1/v2는 응답 텍스트에
[EMOTION]happy[/EMOTION] 같은 태그를 심고 정규식으로 파싱했는데, 파싱 실패
가능성이 있었다. function calling으로 바꾸면 파싱이 아예 필요 없다.

display/main.py(RobotFaceApp)가 포팅된 뒤로는 emotion_queue를 넘기면 그대로
emotion_queue.put(...)으로 화면에 반영된다 — scripts/run_no_robot.py가 실제로
이렇게 연결해서 씀. emotion_queue가 없을 때(예: display 없이 로직만 테스트할
때)는 콘솔 로그만 남기는 폴백으로 동작한다.
"""

VALID_EMOTIONS = (
    "neutral", "happy", "excited", "tender", "scared",
    "angry", "sad", "surprised", "listening", "thinking", "scanning",
)


# 퀴즈 모드별 표정 제한 — 여기 없는 모드(imperfect, 퀴즈 비활성)는 제한 없음.
# - annoying(2026-07-31, 사용자 요청): neutral/thinking만. happy/excited/tender처럼
#   "귀엽게" 보일 수 있는 감정이 섞이면 짜증유발 페르소나의 무뚝뚝함이 흐려진다.
# - all_knowing(2026-08-07, 사용자 요청): neutral 고정. 척척박사는 감정 없이 정답만
#   전달하는 캐릭터라(30단계 톤/제스처 규칙과 같은 취지) 표정 변화 자체를 차단한다.
# 모델이 실수로(또는 페르소나 기본 지시를 따라) 다른 감정을 부르더라도 화면에는
# 반영되지 않는다 — 프롬프트 지시보다 코드 클램프가 확실한 방어선.
_MODE_ALLOWED_EMOTIONS = {
    "annoying": frozenset({"neutral", "thinking"}),
    "all_knowing": frozenset({"neutral"}),
}


def make_set_emotion_tool(emotion_queue=None, quiz_session=None):
    """quiz_session을 넘기면(core/quiz_state.py의 QuizSession), 그 세션이 활성 상태인
    동안 모드별 허용 표정(_MODE_ALLOWED_EMOTIONS) 밖의 감정 요청을 neutral로 눌러준다."""
    def set_emotion(emotion: str) -> str:
        """Set the robot's facial expression to match your current emotional tone.

        Call this once per response, right before or as you start speaking,
        so the robot's face matches what you're about to say.

        Args:
            emotion: one of "neutral", "happy", "excited", "tender", "scared", "angry", "sad", "surprised", "listening", "thinking", "scanning".
        """
        if emotion not in VALID_EMOTIONS:
            print(f"⚠️ 알 수 없는 감정 '{emotion}' — 무시합니다.")
            return f"ignored unknown emotion {emotion}"

        if quiz_session is not None and quiz_session.active:
            allowed = _MODE_ALLOWED_EMOTIONS.get(quiz_session.mode)
            if allowed is not None and emotion not in allowed:
                print(f"⚠️ 퀴즈 모드({quiz_session.mode})에서는 '{emotion}' 대신 neutral로 제한합니다.")
                emotion = "neutral"

        if emotion_queue is not None:
            emotion_queue.put(emotion.upper())
        else:
            print(f"🙂 [emotion] {emotion}")
        return f"emotion set to {emotion}"

    return set_emotion
