"""remember_fact — docs/architecture.md §04의 function-calling 계약.

세션이 시작될 때(Perception이 이름을 확정한 시점) 딱 한 번
make_remember_fact_tool(name)을 호출해 그 이름에 바인딩된 함수를 만들고,
그 함수를 Gemini 모델의 tools=[...]로 넘긴다. 모델은 "누구에 대한
사실인지"를 신경 쓸 필요가 없다 — 세션 = 한 사람이므로 이미 정해져 있다.
"""
from . import profile_manager as profiles


def make_remember_fact_tool(name: str):
    def remember_fact(field: str, value: str, confidence: str = "certain") -> str:
        """Store a fact naturally learned about the person during conversation.

        Call this whenever the person reveals something worth remembering —
        their name, major, MBTI, a hobby, a worry, anything — not only for a
        fixed checklist. Call it again with the same field to correct an
        earlier value; do not ask the person to confirm first unless you are
        genuinely unsure what they meant.

        Args:
            field: what kind of information this is (e.g. "name", "grade", "major", "mbti", "gender", "rc"), or any free-form label if it doesn't fit those.
            value: the value learned, as plain text.
            confidence: "certain" if they stated it directly, "inferred" if you are guessing from context.
        """
        profiles.remember_fact(name, field, value, confidence)
        return f"remembered {field}={value}"

    return remember_fact
