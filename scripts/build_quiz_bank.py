"""퀴즈 문제 은행 작성 도구 — 연구자가 미리 크롭한 사진을 assets/quiz/source/에 넣으면
리사이즈해서 assets/quiz/cropped/에 저장하고, 정답/유의어/힌트를 대화형으로 물어서
assets/quiz/questions.json에 추가한다.

자동 크롭이 아니라 반자동인 이유: 공정성이 중요한 N=30 실험이라 "흥미로운 확대 지점"
선택은 연구자 판단이 필요하다 — 이 스크립트는 이미 크롭된 사진의 리사이즈와 정답
입력만 돕는다. 모든 참가자에게 같은 순서로 보여줘야 하므로(core/quiz_bank.py 참고,
셔플하지 않음) 여기서 추가하는 순서가 곧 실험에서 보여지는 순서다.

사용:
    1. assets/quiz/source/ 에 미리 크롭한 사진들을 넣는다(파일명이 문제 id가 됨, 예: q1.jpg)
    2. (선택, 강력 권장) assets/quiz/source_full/ 에 같은 파일명으로 크롭 전 원본
       사진을 넣는다 — 정답 공개 때 참가자에게 보여줄 사진. 없으면 정답 공개 때도
       확대 사진을 그대로 재사용한다.
    3. python scripts/build_quiz_bank.py
"""
import json
import os
import sys

from PIL import Image

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from bootstrap import ensure_utf8_console

ensure_utf8_console()

SOURCE_DIR = os.path.join(_REPO_ROOT, "assets", "quiz", "source")
CROPPED_DIR = os.path.join(_REPO_ROOT, "assets", "quiz", "cropped")
FULL_SOURCE_DIR = os.path.join(_REPO_ROOT, "assets", "quiz", "source_full")
FULL_DIR = os.path.join(_REPO_ROOT, "assets", "quiz", "full")
BANK_PATH = os.path.join(_REPO_ROOT, "assets", "quiz", "questions.json")
MAX_SIZE = (1200, 1200)
VALID_EXTS = (".jpg", ".jpeg", ".png")


def _find_full_source(qid: str) -> str | None:
    for ext in VALID_EXTS:
        candidate = os.path.join(FULL_SOURCE_DIR, qid + ext)
        if os.path.exists(candidate):
            return candidate
    return None


def _process_full_image(qid: str) -> str | None:
    """source_full/에 qid와 이름이 같은 원본 사진이 있으면 리사이즈해 full/에 저장하고
    저장소 루트 기준 상대경로를 반환한다. 없거나 처리 실패하면 None(호출부가 fallback 처리)."""
    full_source_path = _find_full_source(qid)
    if not full_source_path:
        return None
    try:
        full_img = Image.open(full_source_path)
        full_img.thumbnail(MAX_SIZE)
        full_saved_path = os.path.join(FULL_DIR, f"{qid}.jpg")
        full_img.convert("RGB").save(full_saved_path, "JPEG", quality=90)
        return os.path.relpath(full_saved_path, _REPO_ROOT).replace("\\", "/")
    except Exception as e:
        print(f"⚠️ '{qid}'의 원본 사진 처리 실패({e}) — 정답 공개 때 확대 사진을 대신 사용합니다.")
        return None


def load_existing_bank() -> list:
    if not os.path.exists(BANK_PATH):
        return []
    with open(BANK_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_bank(questions: list):
    with open(BANK_PATH, "w", encoding="utf-8") as f:
        json.dump(questions, f, ensure_ascii=False, indent=2)


def main():
    os.makedirs(SOURCE_DIR, exist_ok=True)
    os.makedirs(CROPPED_DIR, exist_ok=True)
    os.makedirs(FULL_SOURCE_DIR, exist_ok=True)
    os.makedirs(FULL_DIR, exist_ok=True)

    source_files = sorted(
        f for f in os.listdir(SOURCE_DIR) if os.path.splitext(f)[1].lower() in VALID_EXTS
    )
    if not source_files:
        print(f"⚠️ {SOURCE_DIR}에 사진이 없습니다 — 미리 크롭한 사진을 넣고 다시 실행하세요.")
        return

    bank = load_existing_bank()
    existing_ids = {q["id"] for q in bank}

    added = 0
    backfilled = 0
    for filename in source_files:
        qid = os.path.splitext(filename)[0]
        if qid in existing_ids:
            # 이미 등록된 문제라도, 나중에 source_full/에 원본 사진이 추가됐다면
            # full_image_path만 채워 넣는다(정답 공개 사진을 뒤늦게 준비하는 워크플로 지원).
            entry = next(q for q in bank if q["id"] == qid)
            if not entry.get("full_image_path"):
                full_rel_path = _process_full_image(qid)
                if full_rel_path:
                    entry["full_image_path"] = full_rel_path
                    save_bank(bank)
                    backfilled += 1
                    print(f"🔄 '{qid}'에 원본 사진을 뒤늦게 채워 넣었습니다.")
                else:
                    print(f"⏭️  '{qid}'는 이미 등록되어 있어 건너뜁니다(원본 사진도 아직 없음).")
            else:
                print(f"⏭️  '{qid}'는 이미 등록되어 있어 건너뜁니다.")
            continue

        src_path = os.path.join(SOURCE_DIR, filename)
        try:
            img = Image.open(src_path)
            img.thumbnail(MAX_SIZE)
        except Exception as e:
            print(f"❌ '{filename}' 로드 실패, 건너뜁니다: {e}")
            continue

        cropped_path = os.path.join(CROPPED_DIR, f"{qid}.jpg")
        img.convert("RGB").save(cropped_path, "JPEG", quality=90)

        print(f"\n--- {filename} ({qid}) ---")
        answer = input(f"[{qid}] 정답: ").strip()
        if not answer:
            print("❌ 정답이 비어있어 이 문제는 건너뜁니다(파일은 이미 리사이즈됨).")
            continue
        alternates_raw = input("유의어(쉼표로 구분, 없으면 엔터): ").strip()
        alternates = [a.strip() for a in alternates_raw.split(",") if a.strip()]
        hint = input("힌트(선택, 없으면 엔터): ").strip() or None

        full_rel_path = _process_full_image(qid)
        if full_rel_path is None and _find_full_source(qid) is None:
            print(f"⚠️ '{qid}'에 대응하는 원본 사진이 {FULL_SOURCE_DIR}에 없습니다 — "
                  "정답 공개 때 확대 사진을 대신 사용합니다.")

        rel_path = os.path.relpath(cropped_path, _REPO_ROOT).replace("\\", "/")
        bank.append({
            "id": qid, "image_path": rel_path, "answer": answer, "alternates": alternates, "hint": hint,
            "full_image_path": full_rel_path,
        })
        existing_ids.add(qid)
        save_bank(bank)  # 매 문제마다 즉시 저장 — 중간에 멈춰도 이미 입력한 건 안전
        added += 1
        print(f"✅ '{qid}' 추가 완료 (현재 총 {len(bank)}문제)")

    print(f"\n완료 — 이번에 {added}문제 추가, {backfilled}문제 원본 사진 보강, {BANK_PATH}에 총 {len(bank)}문제 저장됨.")
    print("주의: 문제 순서는 이 파일에 적힌 순서 그대로 모든 참가자에게 제시됩니다(셔플 안 함).")


if __name__ == "__main__":
    main()
