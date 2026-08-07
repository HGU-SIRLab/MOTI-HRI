"""세션 종료 시 QuizSession.export_log()를 모드(1/2/3)별로 나눠 user_result/에 저장한다.

core/report_manager.py가 대화록/결과지를 저장하는 것과 같은 "세션 종료 후 파일 쓰기"
계층이지만, 퀴즈 연구 데이터는 형식이 완전히 달라(문항별 정오답 JSON) 별도 모듈로 분리했다.
한 참가자가 세 모드를 로봇을 끄지 않고 한 대화 세션 안에서 연속으로 진행하는 실험 운영
방식(2026-07-31 확정)을 전제로, 세션 종료 시 한 번만 호출되면 그 세션에서 진행된 모드
전부가 각자 파일로 나뉘어 저장된다.
"""
import json
import os
from datetime import datetime

MODE_LABELS = {
    "all_knowing": "1_척척박사",
    "imperfect": "2_하찮미",
    "annoying": "3_짜증유발",
}


def save_quiz_results(user_name: str | None, quiz_log: list[dict]) -> None:
    """quiz_log(QuizSession.export_log()의 결과)를 mode별로 나눠 user_result/ 아래
    참가자+시각 단위 폴더에 저장한다. 빈 로그면 아무것도 안 한다.

    폴더명에 날짜뿐 아니라 시각(HHMMSS)까지 넣는 이유: 같은 참가자를 같은 날 다시
    진행해야 하는 경우(기술적 문제로 재실험 등) 이전 시도를 덮어쓰지 않기 위함 —
    예전 launcher.py는 날짜+이름만으로 파일명을 지어서 이런 재실험 시 데이터가
    조용히 사라질 위험이 있었다.
    """
    if not quiz_log:
        return

    display_name = user_name or "unknown"
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H%M%S")

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    session_dir = os.path.join(base_dir, "user_result", f"{date_str}_{time_str}_{display_name}_quiz")
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
