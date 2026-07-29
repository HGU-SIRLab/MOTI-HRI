"""퀴즈 사진 창(display/quiz_window.py) 독립 테스트 도구.

로봇도 Live API도 필요 없다 — 창이 뜨면 콘솔 메뉴로 규칙/문제/숨김 메시지를 밀어넣어
모니터 배치와 이미지 스케일을 눈으로 확인하는 용도. assets/quiz/questions.json이
있으면 그 사진들을 순서대로 보여준다(없으면 이 스크립트가 자체 생성한 색깔 플레이스홀더
이미지로 대체).

사용:
    python scripts/test_quiz_window.py
    (필요하면 QUIZ_WINDOW_MONITOR_INDEX=1 python scripts/test_quiz_window.py 처럼 모니터 지정)
"""
import multiprocessing
import os
import sys
import tempfile

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from bootstrap import ensure_utf8_console

ensure_utf8_console()


def _load_or_fake_questions():
    from core.quiz_bank import load_question_bank
    try:
        return load_question_bank()
    except Exception as e:
        print(f"⚠️ assets/quiz/questions.json을 못 읽어서({e}) 임시 플레이스홀더로 대체합니다.")
        from PIL import Image
        tmpdir = tempfile.mkdtemp()
        from core.quiz_bank import QuizQuestion
        colors = [(139, 90, 43), (60, 60, 60), (200, 30, 30)]
        fake = []
        for i, color in enumerate(colors):
            path = os.path.join(tmpdir, f"fake{i}.jpg")
            Image.new("RGB", (500, 400), color=color).save(path)
            fake.append(QuizQuestion(id=f"fake{i}", image_path=path, answer=f"테스트정답{i}"))
        return fake


def main():
    from display.quiz_window import quiz_window_process

    questions = _load_or_fake_questions()
    quiz_q: "multiprocessing.Queue" = multiprocessing.Queue()
    proc = multiprocessing.Process(target=quiz_window_process, args=(quiz_q,), daemon=True)
    proc.start()

    menu = f"""
──────────────────────────────
 r) 규칙 안내 텍스트 표시
 1~{len(questions)}) 해당 번호 문제 표시
 v1~v{len(questions)}) 해당 번호 문제의 정답 공개(reveal) 표시 — 원본 사진 확인용
 h) 숨기기(hide)
 q) 종료
──────────────────────────────
"""
    print(menu)
    try:
        while True:
            line = input("명령 > ").strip().lower()
            if line in ("q", "quit", "exit"):
                break
            elif line == "r":
                quiz_q.put({"type": "rules", "text": "부분 확대 사진 퀴즈를 시작합니다! (테스트)"})
            elif line == "h":
                quiz_q.put({"type": "hide"})
            elif line.isdigit() and 1 <= int(line) <= len(questions):
                idx = int(line) - 1
                q = questions[idx]
                quiz_q.put({
                    "type": "question", "index": idx, "total": len(questions),
                    "image_path": q.image_path, "prompt": "이 물건은 무엇일까요?",
                })
            elif line.startswith("v") and line[1:].isdigit() and 1 <= int(line[1:]) <= len(questions):
                idx = int(line[1:]) - 1
                q = questions[idx]
                quiz_q.put({
                    "type": "reveal", "text": f"정답: {q.answer}",
                    "image_path": q.full_image_path or q.image_path,
                })
            else:
                print(f"⚠️ 알 수 없는 명령: {line}")
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        quiz_q.put("__QUIT__")
        proc.join(timeout=3.0)


if __name__ == "__main__":
    main()
