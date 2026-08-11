"""세션 종료 시 QuizSession.export_log()를 모드(1/2/3)별로 나눠 user_result/에 저장한다.

core/report_manager.py가 대화록/결과지를 저장하는 것과 같은 "세션 종료 후 파일 쓰기"
계층이지만, 퀴즈 연구 데이터는 형식이 완전히 달라(문항별 정오답 JSON) 별도 모듈로 분리했다.
한 참가자가 세 모드를 로봇을 끄지 않고 한 대화 세션 안에서 연속으로 진행하는 실험 운영
방식(2026-07-31 확정)을 전제로, 세션 종료 시 한 번만 호출되면 그 세션에서 진행된 모드
전부가 각자 파일로 나뉘어 저장된다.

**2026-08-11: 저장 위치가 참가자별 세션 폴더로 바뀌었다**(core/result_paths.py). 폴더명을
여기서 직접 짓지 않고 launcher.py가 정해준 session_dir을 그대로 쓴다 — 같은 세션의
대화록(core/report_manager.py)과 반드시 같은 폴더에 들어가게 하기 위함.
"""
import json
import os

MODE_LABELS = {
    "all_knowing": "1_척척박사",
    "imperfect": "2_하찮미",
    "annoying": "3_짜증유발",
}


def save_quiz_results(quiz_log: list[dict], session_dir: str) -> None:
    """quiz_log(QuizSession.export_log()의 결과)를 mode별로 나눠 session_dir
    (core/result_paths.make_session_dir())에 저장한다. 빈 로그면 아무것도 안 한다.

    폴더 자체는 여기서 만든다 — 퀴즈를 한 문항도 안 한 세션이 빈 폴더로 남지 않게
    "쓸 게 있을 때만" 생성하는 방식(core/report_manager.py도 동일).
    """
    if not quiz_log:
        return

    os.makedirs(session_dir, exist_ok=True)

    by_mode: dict[str, list[dict]] = {}
    for entry in quiz_log:
        by_mode.setdefault(entry.get("mode", "unknown"), []).append(entry)

    for mode, entries in by_mode.items():
        label = MODE_LABELS.get(mode, mode)
        elapsed_vals = [e.get("elapsed_sec") for e in entries if e.get("elapsed_sec") is not None]
        payload = {
            "mode": mode,
            "mode_label": label,
            "summary": {
                "total_questions": len(entries),
                # 정답률 자체는 조작 점검용일 뿐 가설 검증 지표가 아니다(docs/experiment_design.md
                # §1) — 그래도 현장에서 바로 훑어볼 수 있게 요약만 같이 넣어둔다.
                "user_correct_count": sum(1 for e in entries if e.get("user_correct")),
                "hint_requested_count": sum(1 for e in entries if e.get("hint_requested")),
                # docs/experiment_design.md §5의 로그 지표(2026-08-07 구현) — 문항별 값은
                # results에 그대로 있고, 여기는 현장 확인용 요약만.
                "avg_elapsed_sec": round(sum(elapsed_vals) / len(elapsed_vals), 1) if elapsed_vals else None,
                "annoying_refusals_total": (
                    sum(e.get("annoying_refusals") or 0 for e in entries) if mode == "annoying" else None
                ),
            },
            "results": entries,
        }
        out_path = os.path.join(session_dir, f"{label}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"📄 퀴즈 결과 저장 완료 ({label}): {out_path}")

    # 모드 구분 없이 원본 순서 그대로도 감사/디버깅용으로 남겨둔다.
    combined_path = os.path.join(session_dir, "_전체.json")
    with open(combined_path, "w", encoding="utf-8") as f:
        json.dump(quiz_log, f, ensure_ascii=False, indent=2)
