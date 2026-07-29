"""core/idle_watcher.py의 decide_idle_action() 순수 로직 검증. API 키/로봇/디스플레이
불필요 — 표에 있는 (idle_for, is_sleeping, quiz_active) 조합만 확인한다.

사용:
    python scripts/test_idle_watcher.py
"""
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from bootstrap import ensure_utf8_console

ensure_utf8_console()

from core.idle_watcher import IDLE_SLEEP_SEC, decide_idle_action

CASES = [
    # (idle_for, is_sleeping, quiz_active) -> 기대값
    (0.0, False, False, "none"),
    (IDLE_SLEEP_SEC - 0.1, False, False, "none"),
    (IDLE_SLEEP_SEC, False, False, "sleep"),
    (IDLE_SLEEP_SEC + 100, False, False, "sleep"),
    # 퀴즈 진행 중엔 아무리 조용해도 잠들지 않는다.
    (IDLE_SLEEP_SEC + 100, False, True, "none"),
    # 이미 자는 중 + 계속 조용함 -> 그대로 유지.
    (IDLE_SLEEP_SEC + 5, True, False, "none"),
    # 이미 자는 중 + 방금 말함(idle_for가 작아짐) -> 깨움. 퀴즈 여부 무관(최우선).
    (0.5, True, False, "wake"),
    (0.5, True, True, "wake"),
]


def check(label, condition):
    print(("OK  " if condition else "FAIL") + ": " + label)
    return condition


def main():
    ok = True
    for idle_for, is_sleeping, quiz_active, expected in CASES:
        actual = decide_idle_action(idle_for, is_sleeping, quiz_active)
        label = f"idle_for={idle_for}, is_sleeping={is_sleeping}, quiz_active={quiz_active} -> {actual} (기대: {expected})"
        ok &= check(label, actual == expected)

    print()
    if ok:
        print("✅ 전부 통과")
    else:
        print("❌ 일부 실패")
        sys.exit(1)


if __name__ == "__main__":
    main()
