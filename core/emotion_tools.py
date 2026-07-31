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


# 짜증유발(annoying) 퀴즈 모드 전용 — 사용자 요청(2026-07-31)으로 이 모드에서는
# 표정이 기본(neutral)과 생각하는 중(thinking) 사이에서만 바뀌게 제한한다. 다른
# 감정(특히 happy/excited/tender처럼 "귀엽게" 보일 수 있는 감정)이 섞이면 짜증유발
# 페르소나의 무뚝뚝함이 흐려지기 때문 — 거절 뜸들이기 동안 모델이 실수로(또는 페르소나
# 기본 지시를 따라) 다른 감정을 부르더라도 화면에는 반영되지 않는다.
_ANNOYING_ALLOWED_EMOTIONS = frozenset({"neutral", "thinking"})


def make_set_emotion_tool(emotion_queue=None, quiz_session=None):
    """quiz_session을 넘기면(core/quiz_state.py의 QuizSession), 그 세션이 짜증유발
    모드로 활성 상태인 동안엔 neutral/thinking 이외의 감정 요청을 neutral로 눌러준다."""
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

        if (
            quiz_session is not None
            and quiz_session.active
            and quiz_session.mode == "annoying"
            and emotion not in _ANNOYING_ALLOWED_EMOTIONS
        ):
            print(f"⚠️ 짜증유발 모드에서는 '{emotion}' 대신 neutral로 제한합니다.")
            emotion = "neutral"

        if emotion_queue is not None:
            emotion_queue.put(emotion.upper())
        else:
            print(f"🙂 [emotion] {emotion}")
        return f"emotion set to {emotion}"

    return set_emotion
