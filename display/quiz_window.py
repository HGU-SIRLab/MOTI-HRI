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

WINDOW_WIDTH = 900
WINDOW_HEIGHT = 700
# 삭제된 subtitle.py는 모니터 인덱스를 하드코딩해뒀었다(브리틀) — env var로 바꿀 수 있게 함.
MONITOR_INDEX = int(os.getenv("QUIZ_WINDOW_MONITOR_INDEX", "0"))


def _place_window(root):
    x_pos, y_pos = 0, 0
    if screeninfo:
        try:
            monitors = screeninfo.get_monitors()
            target_index = MONITOR_INDEX if len(monitors) > MONITOR_INDEX else 0
            m = monitors[target_index]
            x_pos = m.x + (m.width - WINDOW_WIDTH) // 2
            y_pos = m.y + (m.height - WINDOW_HEIGHT) // 2
            return root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+{x_pos}+{y_pos}")
        except Exception as e:
            print(f"❌ 퀴즈 창 모니터 확인 오류: {e}. 기본 위치를 사용합니다.")
    else:
        print("⚠️ 'screeninfo' 라이브러리가 없어 기본 위치에 배치합니다.")
    x_pos = (root.winfo_screenwidth() - WINDOW_WIDTH) // 2
    y_pos = (root.winfo_screenheight() - WINDOW_HEIGHT) // 2
    root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+{x_pos}+{y_pos}")


def quiz_window_process(quiz_q: "multiprocessing.Queue"):
    try:
        root = tk.Tk()
        root.title("Moti Quiz")
        root.configure(bg="black")
        root.wm_attributes("-topmost", 1)
        _place_window(root)

        prompt_label = tk.Label(root, text="", font=("Malgun Gothic", 22), fg="white",
                                 bg="black", wraplength=WINDOW_WIDTH - 40, justify="center")
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
                img.thumbnail((WINDOW_WIDTH - 40, WINDOW_HEIGHT - 160))
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
