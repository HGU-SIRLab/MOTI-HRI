"""remember_fact — docs/architecture.md §04의 function-calling 계약.

세션이 시작될 때(Perception이 이름을 확정한 시점) make_remember_fact_tool(name)을
호출해 그 이름에 바인딩된 함수를 만들고, 그 함수를 Gemini 모델의 tools=[...]로
넘긴다. 모델은 "누구에 대한 사실인지"를 신경 쓸 필요가 없다 — 세션 = 한 사람이므로
이미 정해져 있다.

2026-07-27부터: name이 아직 None(처음 보는 사람)이어도 이 툴을 그대로 붙일 수 있다.
name_state={"name": None} 같은 가변 딕셔너리를 넘기면, 모델이 처음으로
remember_fact(field="name", value=...)를 호출하는 순간 그 값을 이름으로 확정하고
(shared_state['detected_user']도 같이 갱신), brain이 주어졌으면 그 시점 shared_state의
얼굴 임베딩으로 즉시 얼굴 등록까지 한다 — Live API 세션은 tools를 세션 도중에 바꿀 수
없어서, "이름을 몰라도 처음부터 붙여두고 첫 호출에서 이름으로 자가부트스트랩" 하는
방식을 택했다. 이름이 확정되기 전에 다른 field로 먼저 부르면 이름부터 알려달라고
안내만 하고 아무것도 저장하지 않는다.
"""
from . import profile_manager as profiles


def make_remember_fact_tool(name_state: dict, shared_state: dict | None = None, brain=None):
    """name_state: {"name": <확정된 이름 또는 None>} — 세션 시작 시점 값을 담아 넘기면,
    첫 remember_fact(field="name", ...) 호출 때 이 딕셔너리가 제자리에서 갱신된다.
    호출자(launcher.py)는 세션이 끝난 뒤 name_state["name"]으로 최종 확정 이름을 읽을 수 있다."""

    def remember_fact(field: str, value: str, confidence: str = "certain") -> str:
        """Store a fact naturally learned about the person during conversation.

        Call this whenever the person reveals something worth remembering —
        their name, major, MBTI, a hobby, a worry, anything — not only for a
        fixed checklist. Call it again with the same field to correct an
        earlier value; do not ask the person to confirm first unless you are
        genuinely unsure what they meant. If you don't know this person's name
        yet, the very first call you make MUST be field="name" — you cannot
        remember anything else about someone before you know who they are.

        Args:
            field: what kind of information this is (e.g. "name", "grade", "major", "mbti", "gender", "rc"), or any free-form label if it doesn't fit those.
            value: the value learned, as plain text.
            confidence: "certain" if they stated it directly, "inferred" if you are guessing from context.
        """
        if name_state["name"] is None:
            if field != "name":
                return "unknown person — call remember_fact(field=\"name\", value=<their name>) first"
            name_state["name"] = value
            if shared_state is not None:
                shared_state['detected_user'] = value
                if brain is not None:
                    emb = shared_state.get('current_face_embedding')
                    if emb is not None:
                        print(brain.register_face(emb, value))

        profiles.remember_fact(name_state["name"], field, value, confidence)
        return f"remembered {field}={value}"

    return remember_fact
