"""퀴즈 문제용 크롭본 만들기 — 원본 사진을 띄워놓고 마우스로 드래그해서 확대 영역을
고르면, 크롭본은 assets/quiz/source/<qid>.jpg에, 원본은 assets/quiz/source_full/<qid>.jpg에
저장한다. 이후 build_quiz_bank.py를 돌리면 정답/힌트를 물어보며 questions.json에 등록된다.

자동 크롭 대신 이 방식인 이유는 build_quiz_bank.py와 동일 — "흥미롭게 헷갈리는 확대
지점" 선택은 연구자 판단이 필요하다.

사용:
    1) 단건: python scripts/crop_quiz_photo.py <원본_이미지_경로> <qid>
       예: python scripts/crop_quiz_photo.py "C:\\Users\\goodj\\...\\파스타면(원본).jpg" 파스타면

    2) 일괄: python scripts/crop_quiz_photo.py
       assets/quiz/source_full/ 에 이미 넣어둔 원본 사진들을 순서대로 훑으면서,
       아직 source/에 크롭본이 없는 것만 창을 띄운다. qid는 파일명에서 "(원본)" 접미사를
       뗀 이름을 그대로 쓴다(예: "파스타면(원본).jpg" → qid "파스타면"). 매 장마다 창을
       닫으면(Enter로 저장) 다음 사진으로 자동으로 넘어간다.
"""
import os
import re
import sys
import tkinter as tk

from PIL import Image, ImageTk

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from bootstrap import ensure_utf8_console

ensure_utf8_console()

SOURCE_DIR = os.path.join(_REPO_ROOT, "assets", "quiz", "source")
FULL_SOURCE_DIR = os.path.join(_REPO_ROOT, "assets", "quiz", "source_full")
DISPLAY_MAX = (1100, 800)  # 화면에 보여줄 때 이보다 크면 축소
MIN_DISPLAY_LONG_SIDE = 500  # 원본이 작아도 이보다 작은 창에서 드래그하면 선택이 부정확해지므로 확대
MIN_CROP_LONG_SIDE = 450  # 저장되는 크롭본이 이보다 작으면 확대(작은 선택/작은 원본 대응)
VALID_EXTS = (".jpg", ".jpeg", ".png")


def _fit_for_display(img: Image.Image) -> Image.Image:
    """선택 작업이 너무 작은 창에서 이뤄지지 않도록, 원본이 크면 DISPLAY_MAX로 축소하고
    작으면 MIN_DISPLAY_LONG_SIDE까지 확대한다(실제 크롭은 항상 원본 해상도 기준이라
    여기서 화질 손실이 나도 최종 크롭 품질과 무관)."""
    long_side = max(img.width, img.height)
    if long_side > max(DISPLAY_MAX):
        scale = min(DISPLAY_MAX[0] / img.width, DISPLAY_MAX[1] / img.height)
    elif long_side < MIN_DISPLAY_LONG_SIDE:
        scale = MIN_DISPLAY_LONG_SIDE / long_side
    else:
        return img.copy()
    size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
    return img.resize(size, Image.LANCZOS)


def _ensure_min_size(img: Image.Image, min_long_side: int = MIN_CROP_LONG_SIDE) -> Image.Image:
    """크롭 결과가 너무 작으면(작은 원본 또는 작은 선택 영역) 자연스럽게 보이도록 확대한다."""
    long_side = max(img.width, img.height)
    if long_side >= min_long_side:
        return img
    scale = min_long_side / long_side
    size = (round(img.width * scale), round(img.height * scale))
    return img.resize(size, Image.LANCZOS)


def crop_one(src_path: str, qid: str) -> bool:
    """창을 띄워 crop 영역을 고르게 하고, 저장되면 True, 창을 그냥 닫으면 False를 반환한다."""
    original = Image.open(src_path).convert("RGB")
    display_img = _fit_for_display(original)
    scale_x = original.width / display_img.width
    scale_y = original.height / display_img.height

    root = tk.Tk()
    root.title(f"크롭 영역 선택 — {qid} (드래그 후 Enter, 다시 하려면 Esc)")

    photo = ImageTk.PhotoImage(display_img)
    canvas = tk.Canvas(root, width=display_img.width, height=display_img.height, cursor="cross")
    canvas.pack()
    canvas.create_image(0, 0, anchor="nw", image=photo)

    state = {"start": None, "rect_id": None, "box": None, "saved": False}

    def on_press(event):
        state["start"] = (event.x, event.y)
        if state["rect_id"] is not None:
            canvas.delete(state["rect_id"])
            state["rect_id"] = None

    def on_drag(event):
        if state["start"] is None:
            return
        if state["rect_id"] is not None:
            canvas.delete(state["rect_id"])
        x0, y0 = state["start"]
        state["rect_id"] = canvas.create_rectangle(
            x0, y0, event.x, event.y, outline="red", width=2
        )

    def on_release(event):
        if state["start"] is None:
            return
        x0, y0 = state["start"]
        x1, y1 = event.x, event.y
        left, right = sorted((max(0, min(x0, display_img.width)), max(0, min(x1, display_img.width))))
        top, bottom = sorted((max(0, min(y0, display_img.height)), max(0, min(y1, display_img.height))))
        if right - left < 5 or bottom - top < 5:
            return
        state["box"] = (left, top, right, bottom)

    def on_save(event=None):
        if state["box"] is None:
            print("⚠️ 먼저 마우스로 영역을 드래그해서 선택하세요.")
            return
        left, top, right, bottom = state["box"]
        crop_box = (
            round(left * scale_x), round(top * scale_y),
            round(right * scale_x), round(bottom * scale_y),
        )
        cropped = _ensure_min_size(original.crop(crop_box))

        os.makedirs(SOURCE_DIR, exist_ok=True)
        cropped_path = os.path.join(SOURCE_DIR, f"{qid}.jpg")
        cropped.save(cropped_path, "JPEG", quality=95)

        print(f"✅ '{qid}' 크롭본 저장 → {cropped_path}")
        state["saved"] = True
        root.destroy()

    def on_cancel(event=None):
        if state["rect_id"] is not None:
            canvas.delete(state["rect_id"])
            state["rect_id"] = None
        state["start"] = None
        state["box"] = None

    def on_skip(event=None):
        print(f"⏭️  '{qid}' 건너뜀")
        root.destroy()

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)
    root.bind("<Return>", on_save)
    root.bind("<Escape>", on_cancel)
    root.bind("<q>", on_skip)

    root.mainloop()
    return state["saved"]


def _derive_qid(filename: str) -> str:
    stem = os.path.splitext(filename)[0]
    return re.sub(r"\s*\(원본\)\s*$", "", stem).strip()


def _existing_crop_ids() -> set:
    if not os.path.isdir(SOURCE_DIR):
        return set()
    return {
        os.path.splitext(f)[0]
        for f in os.listdir(SOURCE_DIR)
        if os.path.splitext(f)[1].lower() in VALID_EXTS
    }


def run_batch():
    if not os.path.isdir(FULL_SOURCE_DIR):
        print(f"⚠️ {FULL_SOURCE_DIR} 가 없습니다 — 원본 사진을 먼저 넣어주세요.")
        return

    full_files = sorted(
        f for f in os.listdir(FULL_SOURCE_DIR) if os.path.splitext(f)[1].lower() in VALID_EXTS
    )
    if not full_files:
        print(f"⚠️ {FULL_SOURCE_DIR} 에 사진이 없습니다.")
        return

    done_ids = _existing_crop_ids()
    pending = []
    for filename in full_files:
        qid = _derive_qid(filename)
        ext = os.path.splitext(filename)[1]
        # build_quiz_bank.py가 qid로 source_full/에서 원본을 찾으므로, "(원본)" 접미사가
        # 붙은 파일명은 qid와 정확히 일치하도록 미리 정리해둔다.
        normalized_path = os.path.join(FULL_SOURCE_DIR, f"{qid}{ext}")
        current_path = os.path.join(FULL_SOURCE_DIR, filename)
        if current_path != normalized_path and not os.path.exists(normalized_path):
            os.rename(current_path, normalized_path)
            print(f"📝 '{filename}' → '{qid}{ext}' 로 이름 정리")
        if qid in done_ids:
            continue
        pending.append((qid, normalized_path))

    if not pending:
        print("모든 원본 사진에 이미 크롭본이 있습니다.")
        return

    print(f"크롭할 사진 {len(pending)}장: {', '.join(qid for qid, _ in pending)}")
    print("각 창에서: 드래그로 영역 선택 → Enter 저장 후 다음 사진, Esc로 다시 선택, q로 건너뛰기\n")

    saved = 0
    for qid, path in pending:
        if crop_one(path, qid):
            saved += 1

    print(f"\n완료 — {saved}/{len(pending)}장 크롭 저장됨.")
    print("다음 단계: python scripts/build_quiz_bank.py 를 실행해서 정답/힌트를 등록하세요.")


def main():
    if len(sys.argv) == 1:
        run_batch()
        return
    if len(sys.argv) != 3:
        print("사용법:\n  일괄: python scripts/crop_quiz_photo.py\n  단건: python scripts/crop_quiz_photo.py <원본_이미지_경로> <qid>")
        return

    src_path, qid = sys.argv[1], sys.argv[2]
    if not os.path.exists(src_path):
        print(f"❌ 파일을 찾을 수 없습니다: {src_path}")
        return
    os.makedirs(FULL_SOURCE_DIR, exist_ok=True)
    full_path = os.path.join(FULL_SOURCE_DIR, f"{qid}.jpg")
    if not os.path.exists(full_path):
        Image.open(src_path).convert("RGB").save(full_path, "JPEG", quality=95)
    if crop_one(full_path, qid):
        print(f"다음 단계: python scripts/build_quiz_bank.py 를 실행해서 '{qid}' 정답/힌트를 등록하세요.")


if __name__ == "__main__":
    main()
