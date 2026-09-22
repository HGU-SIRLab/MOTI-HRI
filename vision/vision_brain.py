# function/vision_brain.py

import os
import pickle
import threading
import numpy as np
from collections import deque, Counter
from insightface.app import FaceAnalysis

# ==========================================
# ⚙️ 설정
# ==========================================
RHO = 0.80          # 경계심 (0~1). 높을수록 엄격하게 구분
ALPHA = 1e-5        # 선택 파라미터
BETA = 0.1          # 학습률
BUFFER_SIZE = 5     # 인식 안정화 버퍼 크기
VOTE_THRESHOLD = 3  # 투표 임계값
# v2는 cwd 상대경로였다 — 실행 위치에 따라 엉뚱한 폴더에 읽고 쓰게 되는 문제가
# 있어(face.py의 모델 경로와 같은 이유로) __file__ 기준 절대경로로 바꿈.
DB_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "art_brain.pkl")

class FuzzyART:
    def __init__(self, rho=RHO, alpha=ALPHA, beta=BETA):
        self.rho = rho
        self.alpha = alpha
        self.beta = beta
        self.W = []     # 기억된 패턴들 (가중치)
        self.labels = [] # 각 패턴의 이름
        self.num_categories = 0

    def _complement_coding(self, x):
        x = np.clip(x, -1, 1)
        x_norm = (x + 1) / 2
        return np.concatenate((x_norm, 1 - x_norm))

    def predict(self, x):
        if self.num_categories == 0:
            return "Unknown", -1

        I = self._complement_coding(x)
        scores = []
        for w in self.W:
            intersection = np.minimum(I, w)
            score = np.sum(intersection) / (self.alpha + np.sum(w))
            scores.append(score)

        sorted_indices = np.argsort(scores)[::-1]
        norm_I = np.sum(I)

        for j in sorted_indices:
            w = self.W[j]
            intersection = np.minimum(I, w)
            if (np.sum(intersection) / norm_I) >= self.rho:
                return self.labels[j], j
        
        return "Unknown", -1

    def learn(self, x, label):
        I = self._complement_coding(x)
        pred_label, idx = self.predict(x)

        # 기존 기억 강화 (Update)
        if idx != -1 and pred_label == label:
            self.W[idx] = self.beta * np.minimum(I, self.W[idx]) + (1 - self.beta) * self.W[idx]
            return f"Updated memory for {label}"

        # 새 기억 생성 (Create)
        self.W.append(I)
        self.labels.append(label)
        self.num_categories += 1
        return f"Created new memory for {label}"

# ==========================================
# ONNX Runtime 실행 프로바이더 (2026-08-24, Jetson 이식)
# ==========================================
# 예전에는 providers=['CUDAExecutionProvider','CPUExecutionProvider']를 그냥 박아뒀다.
# 그 목록은 "요청"일 뿐이라, 설치된 onnxruntime이 CPU 전용 휠이면 **아무 말 없이 CPU로**
# 떨어진다. Windows에서는 그래도 쓸 만했지만 Orin Nano에서 CPU로 떨어지면 얼굴인식이
# 눈에 띄게 느려지는데 원인을 모른 채 헤매게 된다 — 그래서 (1) 실제 가용한 것만 요청하고
# (2) 세션이 뭘로 돌아가는지 시작할 때 찍는다.
#
# Jetson(JetPack)에서는 TensorrtExecutionProvider가 대개 가장 빠르지만 첫 실행 때
# 엔진 빌드로 수 분이 걸린다. 그래서 기본 우선순위에는 넣지 않고, 쓰고 싶으면
# .env의 ORT_PROVIDERS로 명시하게 했다.
_DEFAULT_PROVIDER_ORDER = ["CUDAExecutionProvider", "CPUExecutionProvider"]

# 얼굴 검출 입력 해상도. 640이 기존 값이고 정확도 기준이다. CPU로만 돌려야 하는
# 상황이라면 320으로 낮추면 체감 속도가 크게 오른다(멀리 있는 얼굴은 놓치기 쉬워짐).
DET_SIZE = int(os.getenv("FACE_DET_SIZE", "640"))


def resolve_ort_providers() -> list[str]:
    """요청할 실행 프로바이더 목록을 실제 가용한 것만 남겨서 돌려준다."""
    requested = [p.strip() for p in os.getenv("ORT_PROVIDERS", "").split(",") if p.strip()]
    requested = requested or list(_DEFAULT_PROVIDER_ORDER)
    try:
        import onnxruntime as ort
        available = set(ort.get_available_providers())
    except Exception as e:
        print(f"⚠️ onnxruntime 프로바이더 목록을 못 읽었습니다({e}) — 요청 목록을 그대로 씁니다.")
        return requested

    usable = [p for p in requested if p in available]
    dropped = [p for p in requested if p not in available]
    if dropped:
        print(f"ℹ️ 사용할 수 없어 제외된 프로바이더: {', '.join(dropped)}")
        print(f"   (이 환경에서 가능한 것: {', '.join(sorted(available))})")
    if not usable:
        usable = ["CPUExecutionProvider"]
    return usable


def report_actual_providers(app) -> None:
    """insightface가 만든 세션이 **정말로** 무엇으로 돌고 있는지 출력한다.
    요청 목록이 아니라 세션에 붙은 실제 값을 읽는다."""
    names = set()
    for model in getattr(app, "models", {}).values():
        session = getattr(model, "session", None)
        if session is not None:
            try:
                names.update(session.get_providers())
            except Exception:
                pass
    if not names:
        return
    if names == {"CPUExecutionProvider"}:
        print("⚠️ 얼굴인식이 **CPU**로 돌고 있습니다 (GPU 가속 없음).")
        print("   Jetson이라면 JetPack용 onnxruntime-gpu 휠이 설치됐는지 확인하세요"
              " — `python -c \"import onnxruntime; print(onnxruntime.get_available_providers())\"`"
              " (docs/jetson.md 참고).")
    else:
        print(f"✅ 얼굴인식 실행 프로바이더: {', '.join(sorted(names))}")



class RobotBrain:
    def __init__(self, db_path=None, similarity_threshold=None):
        # (호환성을 위해 인자 추가, 사용은 안 함)
        print("⏳ Vision Brain(InsightFace + FuzzyART) 초기화 중...")
        
        # ▼▼▼ [수정 1] 필요한 모듈만 지정하여 로드 (속도 향상) ▼▼▼
        # allowed_modules=['detection', 'recognition'] 만 사용
        providers = resolve_ort_providers()
        self.app = FaceAnalysis(
            name='buffalo_l',
            allowed_modules=['detection', 'recognition'],
            providers=providers,
        )
        # ctx_id는 GPU 프로바이더가 실제로 잡혔을 때만 0(=GPU)이어야 한다. CPU만 남았는데
        # 0을 주면 insightface가 GPU를 쓰는 줄 알고 진행하다 엉뚱한 데서 실패한다.
        ctx_id = 0 if providers and providers[0] != "CPUExecutionProvider" else -1
        self.app.prepare(ctx_id=ctx_id, det_size=(DET_SIZE, DET_SIZE))
        report_actual_providers(self.app)
        # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲

        # Fuzzy ART (뇌)
        self.art = FuzzyART()
        self.load_brain()

        # face_tracker_worker(추적 스레드)의 recognize_face()와 remember_fact/forget_me
        # 툴(메인 스레드의 asyncio 이벤트 루프)의 register_face()/forget_face()가 self.art.W/
        # labels를 동시에 건드릴 수 있다. learn()은 리스트를 늘리기만 해서 원래도 안전했지만,
        # forget_face()는 리스트를 새로 만들어 통째로 교체하므로(줄어듦) 그 사이에 다른 스레드가
        # predict()에서 읽던 인덱스가 어긋날 수 있다 — 세 메서드를 전부 이 락으로 직렬화한다.
        self._lock = threading.Lock()

        self.buffer = deque(maxlen=BUFFER_SIZE)
        print(f"✅ Vision Brain 준비 완료. (기억된 얼굴 수: {self.art.num_categories})")

    def load_brain(self):
        if os.path.exists(DB_FILE):
            try:
                with open(DB_FILE, 'rb') as f:
                    data = pickle.load(f)
                    self.art.W = data['W']
                    self.art.labels = data['labels']
                    self.art.num_categories = len(data['labels'])
            except Exception as e:
                print(f"⚠️ 브레인 로드 실패: {e}")

    def save_brain(self):
        try:
            with open(DB_FILE, 'wb') as f:
                pickle.dump({'W': self.art.W, 'labels': self.art.labels}, f)
            print("💾 얼굴 기억(Brain) 저장 완료.")
        except Exception as e:
            print(f"❌ 얼굴 기억 저장 실패: {e}")

    def recognize_face(self, frame):
        """
        프레임을 받아 얼굴 벡터를 추출하고, FuzzyART로 누구인지 식별합니다.
        Return: (embedding, predicted_name)
        """
        # [참고] CPU 모드일 때 여기서 시간이 가장 많이 소요됩니다.
        # face.py에서 1초에 한 번만 호출하도록 제한했으므로 이제 괜찮을 것입니다.
        faces = self.app.get(frame)
        
        if len(faces) == 0:
            self.buffer.append(None)
            return None, None

        # 화면 중앙에 가장 가까운 얼굴 선택
        h, w, _ = frame.shape
        cx = w // 2
        target = min(faces, key=lambda f: abs((f.bbox[0]+f.bbox[2])/2 - cx))
        embedding = target.embedding

        # 뇌에 물어보기
        with self._lock:
            raw_name, _ = self.art.predict(embedding)
        self.buffer.append(raw_name)

        # 투표 (안정화)
        valid = [n for n in self.buffer if n is not None]
        if valid:
            common, cnt = Counter(valid).most_common(1)[0]
            if cnt >= VOTE_THRESHOLD:
                return embedding, common
        
        return embedding, "Thinking..."

    def register_face(self, embedding, name):
        """외부에서 이름이 확인되면 기억에 등록"""
        if embedding is None: return "No face"
        with self._lock:
            msg = self.art.learn(embedding, name)
            self.save_brain() # 즉시 저장
        return msg

    def relabel_face(self, old: str, new: str) -> int:
        """등록된 얼굴의 이름표만 바꾼다 — 전사 오인식으로 잘못 붙은 이름 정정용.

        forget_face + register_face로 대신하면 학습된 카테고리(W)를 버리고 그 순간의
        임베딩 하나로 다시 배우게 된다. 이름만 틀렸을 뿐 얼굴은 맞게 학습돼 있으므로
        라벨만 갈아끼우는 편이 인식 정확도를 지킨다. 한 사람이 여러 카테고리로 나뉘어
        있을 수 있어(forget_face 주석 참고) 일치하는 전부를 바꾼다.
        """
        if not old or not new or old == new:
            return 0
        with self._lock:
            changed = 0
            for i, label in enumerate(self.art.labels):
                if label == old:
                    self.art.labels[i] = new
                    changed += 1
            if changed:
                self.save_brain()
        return changed

    def forget_face(self, name: str) -> int:
        """해당 이름으로 기억된 얼굴 카테고리를 전부 지운다(사용자 삭제 요청용).

        같은 사람이 인식 임계값을 살짝 벗어나 여러 카테고리로 나뉘어 저장돼 있을 수
        있어(W/labels는 병렬 리스트, 한 이름당 항목이 여럿일 수 있음) 이름이 일치하는
        전부를 제거한다. 반환값은 제거된 카테고리 수(0이면 애초에 기억이 없었던 것).
        """
        with self._lock:
            keep = [i for i, label in enumerate(self.art.labels) if label != name]
            removed = len(self.art.labels) - len(keep)
            if removed:
                self.art.W = [self.art.W[i] for i in keep]
                self.art.labels = [self.art.labels[i] for i in keep]
                self.art.num_categories = len(self.art.labels)
                self.save_brain()
        return removed