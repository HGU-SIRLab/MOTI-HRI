"""사용자 프로필 저장소.

docs/architecture.md §04 — 고정 슬롯(STAGES) 대신 자유 key-value facts를
누적한다. 같은 field로 다시 부르면 정정(덮어쓰기)으로 취급한다.
"""
import json
import os
import threading
from datetime import datetime, timezone

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
        except json.JSONDecodeError:
            return {}


def _save_all(data: dict):
    with open(_PROFILES_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


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
