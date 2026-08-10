"""세션 종료 대화록 저장(core/report_manager.py) 독립 테스트.

**2026-08-10부터 API 키가 필요 없다** — '마음 처방전'(LLM 결과지) 생성을 제거하고 대화록
파일 저장만 남겼기 때문(제거 이유는 core/report_manager.py 상단 주석 참고). 따라서 이제
이 스크립트도 완전한 오프라인 테스트다.

사용:
    python scripts/test_report.py
"""
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from bootstrap import ensure_utf8_console

ensure_utf8_console()

from core import report_manager

TEST_USER = "__테스트유저__"


def check(label, condition):
    print(("OK  " if condition else "FAIL") + ": " + label)
    return condition


def main():
    ok = True
    result_dir = os.path.join(_REPO_ROOT, "user_result")

    def test_files():
        if not os.path.exists(result_dir):
            return []
        return [f for f in os.listdir(result_dir) if TEST_USER in f]

    # 이전 실행이 남긴 파일만 골라서 지운다 — 공유 폴더를 통째로 비우지 않는다
    # (실제 참가자 데이터가 같은 폴더에 있다).
    for f in test_files():
        os.remove(os.path.join(result_dir, f))

    conversation_log = (
        "User: 안녕 모티야, 오늘 팀플 때문에 너무 지쳐 | Moti: 팀플 때문에 많이 지치셨군요, 어떤 부분이 제일 힘드셨어요?\n"
        "User: 조원들이 다 잠수타서 나 혼자 다 했어 | Moti: 혼자서 다 짊어지셨다니 정말 고생 많으셨어요."
    )
    report_manager.save_conversation_log(TEST_USER, conversation_log)

    files = test_files()
    ok &= check(f"대화록 파일 1개가 생성된다 ({files})", len(files) == 1)
    if files:
        text = open(os.path.join(result_dir, files[0]), encoding="utf-8").read()
        ok &= check("대화록에 사용자 발화와 모티 응답이 모두 들어있다",
                    "조원들이 다 잠수타서" in text and "혼자서 다 짊어지셨다니" in text)
        # 회귀 방지: 결과지(마음 처방전)는 더 이상 만들지 않는다 — 다시 생기면 세션마다
        # 대화록 전체를 프롬프트에 넣는 배치 호출이 부활한 것이다.
        ok &= check("'마음 처방전' 결과지는 더 이상 생성되지 않는다",
                    not any("결과지" in f for f in files))

    ok &= check("이름을 모르면 아무것도 저장하지 않는다",
                report_manager.save_conversation_log("", conversation_log) is None
                and len(test_files()) == len(files))

    for f in test_files():
        os.remove(os.path.join(result_dir, f))

    print()
    if ok:
        print("✅ 전부 통과")
    else:
        print("❌ 일부 실패")
        sys.exit(1)


if __name__ == "__main__":
    main()
