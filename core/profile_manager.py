"""사용자 프로필 저장소.

docs/architecture.md §04 — 고정 슬롯(STAGES) 대신 자유 key-value facts를
누적한다. 같은 field로 다시 부르면 정정(덮어쓰기)으로 취급한다.
"""
import json
import os
import shutil
import threading
from datetime import datetime, timezone

MODEL_NAME = os.getenv("MODEL_NAME", "gemini-3.1-flash-lite")

_LOCK = threading.Lock()
_PROFILES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "user_profiles.json"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_all() -> dict:
    if not os.path.exists(_PROFILES_PATH):
        return {}
    with open(_PROFILES_PATH, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as e:
            # 2026-07-31 코드 리뷰로 발견: 예전엔 여기서 조용히 {}를 반환했다 — 그러면
            # 다음 remember_fact() 호출이 "원래 비어있던 저장소"로 착각해서 지금까지 쌓인
            # 모든 참가자 프로필을 통째로 덮어써버리는 사고로 이어질 수 있었다(N=30 연구
            # 데이터가 전부 이 파일 하나에 누적됨). 손상된 원본을 먼저 백업해두고 크게
            # 경고한다 — 저장소가 원래부터 빈 것과 파일이 손상된 것을 헷갈리지 않도록.
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup_path = f"{_PROFILES_PATH}.corrupted-{timestamp}"
            try:
                shutil.copy2(_PROFILES_PATH, backup_path)
                backup_note = f"손상된 원본은 백업해뒀습니다: {backup_path}"
            except OSError as backup_err:
                backup_note = f"손상된 원본 백업도 실패했습니다({backup_err}) — 수동으로 확인하세요: {_PROFILES_PATH}"
            print(
                f"🚨 user_profiles.json 파싱 실패({e}) — 빈 저장소로 시작합니다. "
                f"이대로 remember_fact가 호출되면 저장 시점에 빈 데이터로 덮어써집니다! {backup_note}"
            )
            return {}


def _save_all(data: dict):
    # 쓰는 도중 프로세스가 죽으면(Ctrl+C, 크래시, 정전 등) 파일이 손상된 채로 남을 수
    # 있었다 — 임시 파일에 먼저 쓰고 os.replace()로 원자적으로 교체해, 쓰기가 절반만
    # 진행된 상태의 파일이 디스크에 남는 경우 자체를 없앤다(2026-07-31 코드 리뷰).
    tmp_path = f"{_PROFILES_PATH}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, _PROFILES_PATH)


def remember_fact(name: str, field: str, value: str, confidence: str = "certain") -> dict:
    """사실을 저장한다. 같은 field가 이미 있으면 값을 갱신(정정)한다."""
    with _LOCK:
        data = _load_all()
        profile = data.setdefault(name, {"facts": [], "created_at": _now()})
        profile["last_seen"] = _now()

        for fact in profile["facts"]:
            if fact["field"] == field:
                fact["value"] = value
                fact["confidence"] = confidence
                fact["updated_at"] = _now()
                break
        else:
            profile["facts"].append({
                "field": field,
                "value": value,
                "confidence": confidence,
                "updated_at": _now(),
            })

        _save_all(data)
        return profile


def get_facts(name: str) -> list:
    with _LOCK:
        data = _load_all()
    return data.get(name, {}).get("facts", [])


def is_known(name: str) -> bool:
    with _LOCK:
        data = _load_all()
    return name in data


def load_profile_for_chat(name: str) -> str | None:
    """시스템 인스트럭션에 주입할 요약 문자열. 모르는 사람이면 None."""
    facts = get_facts(name)
    if not facts:
        return None
    lines = []
    for f in facts:
        suffix = "" if f["confidence"] == "certain" else " (추정)"
        lines.append(f"- {f['field']}: {f['value']}{suffix}")
    return "\n".join(lines)


def touch_last_seen(name: str):
    """대화 없이 얼굴만 재인식된 경우에도 last_seen만 갱신하고 싶을 때."""
    with _LOCK:
        data = _load_all()
        if name in data:
            data[name]["last_seen"] = _now()
            _save_all(data)


def forget_user(name: str):
    """테스트/관리 목적 — 특정 사용자의 프로필을 완전히 삭제한다."""
    with _LOCK:
        data = _load_all()
        if name in data:
            del data[name]
            _save_all(data)


def consolidate_facts(name: str, max_facts: int = 20) -> bool:
    """facts가 max_facts개를 넘으면 LLM으로 유사 field 병합/압축한다.

    field가 완전 자유형이라(memory_tools.py 계약) 모델이 세션마다 "고민"/"요즘고민"처럼
    같은 의미를 다른 이름으로 저장하면 무한정 늘어날 수 있다(v2의 batch_update_summary가
    하던 정리 작업이 v3엔 없었음 — docs/integration-points.md). launcher.py가 세션 종료
    시 호출하는 것을 전제로 한다. 파싱/API 실패 등 어떤 이유로든 결과를 신뢰할 수 없으면
    기존 facts를 그대로 두고 False를 반환한다 — 정리 실패가 데이터 손실로 이어지지 않게 함.
    """
    facts = get_facts(name)
    if len(facts) <= max_facts:
        return False

    import google.generativeai as genai

    from core.utils import _extract_text

    facts_json = json.dumps(
        [{"field": f["field"], "value": f["value"], "confidence": f["confidence"]} for f in facts],
        ensure_ascii=False,
    )
    prompt = f"""아래 JSON은 한 사람에 대해 여러 대화 세션에 걸쳐 자유형으로 쌓인 사실 목록입니다.
같은 의미인데 field 이름만 다른 항목(예: "고민"과 "요즘고민")은 하나로 합치고, 서로 내용이
모순되면 더 최근/구체적인 쪽을 남기세요. 전체를 최대 {max_facts}개 이하로 압축하되, 이름/학년/
전공/MBTI처럼 신원에 가까운 안정적인 정보는 절대 빠뜨리지 마세요. 다른 설명 없이 오직 JSON
배열만 출력하세요. 각 원소 형식: {{"field": string, "value": string, "confidence": "certain" 또는 "inferred"}}.

[원본 facts]
{facts_json}
"""
    try:
        model = genai.GenerativeModel(MODEL_NAME)
        response = model.generate_content(prompt)
        text = _extract_text(response).strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
        try:
            consolidated = json.loads(text)
        except json.JSONDecodeError:
            # 모델이 코드펜스 뒤에 여분의 설명을 덧붙이는 등 앞뒤로 잡음이 섞이면
            # 위 코드펜스 제거만으로는 부족할 수 있다 — 첫 '['부터 마지막 ']'까지만
            # 잘라 한 번 더 시도(그래도 실패하면 바깥 except가 원본을 그대로 지킨다).
            start, end = text.find("["), text.rfind("]")
            if start == -1 or end == -1 or end < start:
                raise
            consolidated = json.loads(text[start:end + 1])

        cleaned = []
        now = _now()
        for item in consolidated[:max_facts]:
            field, value = item["field"], item["value"]
            cleaned.append({
                "field": str(field),
                "value": str(value),
                "confidence": item.get("confidence", "certain"),
                "updated_at": now,
            })
        if not cleaned:
            raise ValueError("consolidated facts came back empty")
    except Exception as e:
        print(f"⚠️ facts 정리 실패, 원본 유지: {e}")
        return False

    with _LOCK:
        data = _load_all()
        if name in data:
            data[name]["facts"] = cleaned
            _save_all(data)
    print(f"🧹 {name}님 facts 정리: {len(facts)}개 -> {len(cleaned)}개")
    return True
