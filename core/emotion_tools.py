"""set_emotion — docs/architecture.md §06. v1/v2는 응답 텍스트에
[EMOTION]happy[/EMOTION] 같은 태그를 심고 정규식으로 파싱했는데, 파싱 실패
가능성이 있었다. function calling으로 바꾸면 파싱이 아예 필요 없다.

display/가 아직 없어서 지금은 콘솔 로그만 남긴다. display/main.py가 생기면
emotion_queue를 넘겨서 그대로 emotion_queue.put(...)으로 바꾸면 된다.
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
