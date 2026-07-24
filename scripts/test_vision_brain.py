"""얼굴인식(vision/vision_brain.py) 독립 테스트 도구.

노트북 웹캠만 있으면 로봇 없이도 동작한다 — 팬/틸트 서보를 쓰지 않기 때문.
화면에 인식된 이름이 표시된다. 'r'을 누르면 지금 보이는 얼굴을 이름과 함께
등록(학습)한다. ESC로 종료 시 art_brain.pkl에 자동 저장됨(RobotBrain.register_face가
등록 시점에 이미 저장하므로 사실상 즉시 반영).

사용:
    python scripts/test_vision_brain.py [카메라 인덱스]
"""
import os
import sys
import time

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from bootstrap import ensure_utf8_console

ensure_utf8_console()

from core import suppress
from vision.vision_brain import RobotBrain


def main():
    camera_index = int(sys.argv[1]) if len(sys.argv) > 1 else 0

    cv2, _ = suppress.import_cv2_mp()

    print("▶ RobotBrain 초기화 중 (모델 로딩)...")
    brain = RobotBrain()

    cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        print(f"⚠️ 카메라({camera_index}) 열기 실패")
        return
    print(f"✅ 카메라({camera_index}) 열림 — 'r': 등록, ESC: 종료")

    last_embedding = None
    last_recog_time = 0.0
    RECOG_INTERVAL = 0.5
    display_name = "..."

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frame = cv2.flip(frame, 1)

            now = time.time()
            if now - last_recog_time >= RECOG_INTERVAL:
                last_recog_time = now
                emb, name = brain.recognize_face(frame)
                last_embedding = emb
                display_name = name if name else "Unknown"

            cv2.putText(frame, display_name, (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 0), 2)
            cv2.imshow("Vision Brain Test", frame)

            key = cv2.waitKey(1) & 0xFF
            if key == 27:  # ESC
                break
            elif key == ord('r'):
                if last_embedding is None:
                    print("⚠️ 등록할 얼굴이 없습니다 (인식된 얼굴 없음).")
                    continue
                name = input("등록할 이름 입력 > ").strip()
                if name:
                    msg = brain.register_face(last_embedding, name)
                    print(f"✅ {msg}")
    finally:
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
