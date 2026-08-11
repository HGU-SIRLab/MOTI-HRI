"""세션 산출물 저장 독립 테스트 — 대화록(core/report_manager.py) + 퀴즈 결과
(core/quiz_export.py)가 참가자별 세션 폴더(core/result_paths.py) 하나에 함께 저장되는지.

**2026-08-10부터 API 키가 필요 없다** — '마음 처방전'(LLM 결과지) 생성을 제거하고 대화록
파일 저장만 남겼기 때문(제거 이유는 core/report_manager.py 상단 주석 참고). 따라서 이제
이 스크립트도 완전한 오프라인 테스트다.

사용:
    python scripts/test_report.py
"""
import json
import os
import shutil
import sys
from datetime import datetime

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from bootstrap import ensure_utf8_console

ensure_utf8_console()

from core import report_manager, result_paths
from core.quiz_export import save_quiz_results

# 실제 참가자 폴더와 절대 겹치지 않을 이름을 쓴다 — 이 테스트는 자기가 만든 이
# 폴더 하나만 지우고, user_result/의 다른 것은 절대 건드리지 않는다.
TEST_PARTICIPANT = "__테스트참가자__"


def check(label, condition):
    print(("OK  " if condition else "FAIL") + ": " + label)
    return condition


def main():
    ok = True
    participant_root = os.path.join(result_paths.RESULT_ROOT, TEST_PARTICIPANT)

    # 이전 실행이 남긴 테스트 폴더만 골라서 지운다 — 공유 폴더를 통째로 비우지 않는다
    # (실제 참가자 데이터가 같은 곳에 있다).
    if os.path.isdir(participant_root):
        shutil.rmtree(participant_root)

    # --- PARTICIPANT_ID 검증: 잘못된 값은 조용히 넘어가지 않고 죽어야 한다 ---
    def rejects(raw):
        try:
            result_paths.parse_participant_id(raw)
            return False
        except ValueError:
            return True

    ok &= check("정상 참가자ID는 통과한다", result_paths.parse_participant_id(" P07 ") == "P07")
    ok &= check("빈 PARTICIPANT_ID는 거부된다", rejects(None) and rejects("") and rejects("   "))
    ok &= check("경로 구분자가 든 PARTICIPANT_ID는 거부된다",
                rejects("../P07") and rejects("P07/1") and rejects("P:07"))

    # --- 대화록 + 퀴즈 결과가 같은 폴더에 저장되는지 ---
    session_dir = result_paths.make_session_dir(TEST_PARTICIPANT, datetime.now())
    ok &= check("쓰기 전에는 폴더가 만들어지지 않는다", not os.path.exists(session_dir))

    conversation_log = (
        "User: 안녕 모티야, 오늘 팀플 때문에 너무 지쳐 | Moti: 팀플 때문에 많이 지치셨군요, 어떤 부분이 제일 힘드셨어요?\n"
        "User: 조원들이 다 잠수타서 나 혼자 다 했어 | Moti: 혼자서 다 짊어지셨다니 정말 고생 많으셨어요."
    )
    report_manager.save_conversation_log("__테스트유저__", conversation_log, session_dir)

    quiz_log = [
        {"mode": "all_knowing", "question_id": "q1", "user_correct": True,
         "hint_requested": False, "elapsed_sec": 12.0},
        {"mode": "imperfect", "question_id": "q2", "user_correct": False,
         "hint_requested": True, "elapsed_sec": 20.0},
        {"mode": "annoying", "question_id": "q3", "user_correct": False,
         "hint_requested": True, "elapsed_sec": 30.0, "annoying_refusals": 2},
    ]
    save_quiz_results(quiz_log, session_dir)
    result_paths.write_session_meta(
        session_dir, participant_id=TEST_PARTICIPANT,
        quiz_mode_order=["1번 척척박사", "2번 하찮미", "3번 짜증유발"],
    )

    files = sorted(os.listdir(session_dir)) if os.path.isdir(session_dir) else []
    ok &= check(f"한 세션 폴더에 대화록/모드별 JSON/메타가 모두 들어있다 ({files})",
                files == ["1_척척박사.json", "2_하찮미.json", "3_짜증유발.json",
                          "_전체.json", "session_meta.json", "대화.txt"])

    if "대화.txt" in files:
        text = open(os.path.join(session_dir, "대화.txt"), encoding="utf-8").read()
        ok &= check("대화록에 사용자 발화와 모티 응답이 모두 들어있다",
                    "조원들이 다 잠수타서" in text and "혼자서 다 짊어지셨다니" in text)
    # 회귀 방지: 결과지(마음 처방전)는 더 이상 만들지 않는다 — 다시 생기면 세션마다
    # 대화록 전체를 프롬프트에 넣는 배치 호출이 부활한 것이다.
    ok &= check("'마음 처방전' 결과지는 더 이상 생성되지 않는다",
                not any("결과지" in f for f in files))

    if "3_짜증유발.json" in files:
        payload = json.load(open(os.path.join(session_dir, "3_짜증유발.json"), encoding="utf-8"))
        ok &= check("모드별 파일에 요약과 문항이 그대로 들어간다",
                    payload["summary"]["total_questions"] == 1
                    and payload["summary"]["annoying_refusals_total"] == 2
                    and payload["results"][0]["question_id"] == "q3")

    if "session_meta.json" in files:
        meta = json.load(open(os.path.join(session_dir, "session_meta.json"), encoding="utf-8"))
        # 카운터밸런싱 그룹은 예전엔 .env에만 있어서 분석 때 손으로 맞춰야 했다.
        ok &= check("메타에 참가자ID와 배정된 모드 순서가 남는다",
                    meta["participant_id"] == TEST_PARTICIPANT
                    and meta["quiz_mode_order"][1] == "2번 하찮미")

    # --- 이름을 몰라도 대화록이 저장되어야 한다(예전엔 통째로 사라졌다) ---
    anon_dir = result_paths.make_session_dir(TEST_PARTICIPANT,
                                             datetime.now().replace(year=2000))
    report_manager.save_conversation_log(None, conversation_log, anon_dir)
    anon_path = os.path.join(anon_dir, "대화.txt")
    ok &= check("이름을 몰라도 대화록은 저장된다",
                os.path.exists(anon_path)
                and "이름 미확인" in open(anon_path, encoding="utf-8").read())

    # --- 빈 결과는 폴더를 만들지 않는다 ---
    empty_dir = result_paths.make_session_dir(TEST_PARTICIPANT,
                                              datetime.now().replace(year=2001))
    save_quiz_results([], empty_dir)
    ok &= check("퀴즈 로그가 비면 폴더를 만들지 않는다", not os.path.exists(empty_dir))
    ok &= check("아무것도 안 남긴 세션엔 메타도 안 쓴다",
                result_paths.write_session_meta(empty_dir, participant_id=TEST_PARTICIPANT) is None)

    if os.path.isdir(participant_root):
        shutil.rmtree(participant_root)

    print()
    if ok:
        print("✅ 전부 통과")
    else:
        print("❌ 일부 실패")
        sys.exit(1)


if __name__ == "__main__":
    main()
