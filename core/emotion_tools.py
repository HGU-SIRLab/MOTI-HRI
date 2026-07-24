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


def make_set_emotion_tool(emotion_queue=None):
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

        if emotion_queue is not None:
            emotion_queue.put(emotion.upper())
        else:
            print(f"🙂 [emotion] {emotion}")
        return f"emotion set to {emotion}"

    return set_emotion
