"""퀴즈 사진을 보여주는 별도 창 — 삭제됐던 display/subtitle.py(git show 7bedd39~1:display/subtitle.py로
복원 가능)와 같은 패턴을 따른다: 별도 multiprocessing.Process + Tkinter + Queue, screeninfo로
모니터 선택. display/main.py의 pygame 얼굴 UI와 완전히 다른 프로세스라, 이 새 코드의 버그가
이미 검증된 얼굴 UI를 절대 건드리지 않는다.

메시지 프로토콜(quiz_q에 dict 또는 "__QUIT__" 문자열을 push):
    {"type": "rules", "text": str}
    {"type": "question", "index": int, "total": int, "image_path": str, "prompt": str}
    {"type": "reveal", "text": str, "image_path": str}  # image_path는 크롭 전 원본 사진
    {"type": "hide"}
"""
import multiprocessing
import os
import tkinter as tk
from queue import Empty

from PIL import Image, ImageTk

try:
    import screeninfo
except ImportError:
    screeninfo = None

# 삭제된 subtitle.py는 모니터 인덱스를 하드코딩해뒀었다(브리틀) — env var로 바꿀 수 있게 함.
MONITOR_INDEX = int(os.getenv("QUIZ_WINDOW_MONITOR_INDEX", "0"))
# 항상 위(topmost)는 실제 로봇 운용 때는 필요하지만, scripts/test_quiz_window.py처럼 같은
# 화면에서 터미널에 명령을 입력하며 테스트할 때는 전체화면 창이 터미널을 가려서 타이핑이
# 안 되는 문제가 있다 — 그럴 땐 0으로 꺼서 Alt+Tab/클릭으로 터미널을 앞으로 꺼낼 수 있게 한다.
TOPMOST = os.getenv("QUIZ_WINDOW_TOPMOST", "1") != "0"


def _place_fullscreen(root) -> tuple[int, int]:
    """선택한 모니터 전체를 채우는 테두리 없는 창으로 배치하고 (width, height)를 반환한다."""
    if screeninfo:
        try:
            monitors = screeninfo.get_monitors()
            target_index = MONITOR_INDEX if len(monitors) > MONITOR_INDEX else 0
            m = monitors[target_index]
            root.geometry(f"{m.width}x{m.height}+{m.x}+{m.y}")
            root.overrideredirect(True)
            return m.width, m.height
        except Exception as e:
            print(f"❌ 퀴즈 창 모니터 확인 오류: {e}. 기본 화면 전체로 대체합니다.")
    else:
        print("⚠️ 'screeninfo' 라이브러리가 없어 기본 화면에 전체화면으로 배치합니다.")
    w, h = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{w}x{h}+0+0")
    root.overrideredirect(True)
    return w, h


def quiz_window_process(quiz_q: "multiprocessing.Queue"):
    try:
        root = tk.Tk()
        root.title("Moti Quiz")
        root.configure(bg="black")
        if TOPMOST:
            root.wm_attributes("-topmost", 1)
        win_w, win_h = _place_fullscreen(root)
        # overrideredirect(테두리 없는 전체화면)는 닫기 버튼이 없어 갇힐 수 있으므로 탈출구를
        # 두되, TOPMOST=1인 실제 로봇 운용 모드에서는 절대 바인딩하지 않는다 — 실험 중
        # 실수로 Esc를 눌러 이 프로세스가 조용히 죽으면(daemon이라 launcher.py가 감지 못함)
        # 그 세션 내내 퀴즈 화면이 복구 불가능하게 사라진다. 개발/테스트(TOPMOST=0)에서만 유효.
        if not TOPMOST:
            root.bind("<Escape>", lambda e: root.destroy())

        prompt_label = tk.Label(root, text="", font=("Malgun Gothic", 22), fg="white",
                                 bg="black", wraplength=win_w - 80, justify="center")
        prompt_label.pack(pady=(20, 10))

        image_label = tk.Label(root, bg="black")
        image_label.pack(expand=True, fill="both")

        progress_label = tk.Label(root, text="", font=("Malgun Gothic", 14), fg="#AAAAAA", bg="black")
        progress_label.pack(pady=(0, 20))

        # Tkinter는 PhotoImage에 대한 강한 참조가 없으면 가비지 컬렉션으로 이미지가
        # 사라진다(빈 화면이 되는 흔한 함정) — 딕셔너리에 붙잡아둔다.
        _current_photo = {"img": None}

        def _clear():
            image_label.configure(image="")
            _current_photo["img"] = None
            progress_label.configure(text="")

        def _display_image(path):
            try:
                img = Image.open(path)
                max_w, max_h = win_w - 80, win_h - 220
                # thumbnail()은 축소만 하므로, 원본이 작은 크롭 사진(부분 확대라 원래
                # 작을 수 있음)은 창 안에서 우표만 하게 나온다 — 확대도 함께 지원해
                # 항상 창을 자연스럽게 채우도록 한다.
                scale = min(max_w / img.width, max_h / img.height)
                img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                image_label.configure(image=photo)
                _current_photo["img"] = photo
            except Exception as e:
                print(f"❌ 퀴즈 이미지 로드 실패({path}): {e}")
                image_label.configure(image="")
                _current_photo["img"] = None

        def _show_question(msg):
            prompt_label.configure(text=msg.get("prompt", "이 물건은 무엇일까요?"))
            _display_image(msg["image_path"])
            total, index = msg.get("total"), msg.get("index")
            if total is not None and index is not None:
                progress_label.configure(text=f"{index + 1} / {total}")

        def _show_reveal(msg):
            prompt_label.configure(text=msg.get("text", ""))
            path = msg.get("image_path")
            if path:
                _display_image(path)
            else:
                image_label.configure(image="")
                _current_photo["img"] = None
            progress_label.configure(text="")

        def check_queue():
            try:
                msg = quiz_q.get_nowait()
                if msg == "__QUIT__":
                    root.destroy()
                    return
                msg_type = msg.get("type")
                if msg_type == "rules":
                    _clear()
                    prompt_label.configure(text=msg.get("text", ""))
                elif msg_type == "question":
                    _show_question(msg)
                elif msg_type == "reveal":
                    _show_reveal(msg)
                elif msg_type == "hide":
                    _clear()
            except Empty:
                pass
            root.after(150, check_queue)

        print("🖼️ 퀴즈 창 프로세스 시작됨.")
        check_queue()
        root.mainloop()
    except Exception as e:
        print(f"❌ 퀴즈 창 프로세스 오류: {e}")
    finally:
        print("🛑 퀴즈 창 프로세스 종료됨.")
