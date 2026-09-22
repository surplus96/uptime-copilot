import random
from dataclasses import dataclass

SIGNALS = ["volt", "rotate", "pressure", "vibration"]
SIGNAL_TO_ERROR = {"volt": "error1", "rotate": "error2", "pressure": "error3", "vibration": "error4"}
SIGNAL_TO_COMPONENT = {"volt": "comp1", "rotate": "comp2", "pressure": "comp3", "vibration": "comp4"}

ERROR_IDS = ["error1", "error2", "error3", "error4", "error5"]
ERROR_WEIGHTS = [0.26, 0.25, 0.21, 0.19, 0.09]   # 실제 오류 종류 분포

P_EPISODE = 0.00087            # 설비·시간당 열화 시작 확률 (연 7.6회 ÷ 8760)
LEAD_HOURS = (44, 52)          # 열화 시작 ~ 고장까지
RAMP_UP_HOURS = 6              # 편차가 목표 크기까지 오르는 시간
DRIFT_MEAN, DRIFT_STD = 1.6, 0.4
DRIFT_CLIP = (0.8, 2.6)
STRONG_FRACTION = 0.15         # 데모용: 이 비율은 3σ 이상 강한 열화 (실제는 1.2%)
STRONG_RANGE = (3.2, 4.5)
UP_PROBABILITY = 0.66          # 편차가 (+)방향일 확률
P_PRECURSOR_ERROR = 0.05       # 열화 중 시간당 전조 오류 확률
SAME_ERROR_PROB = 0.64         # 전조 오류가 대응 신호의 오류일 확률
P_BG_ERROR = 0.0005             # 고장과 무관한 배경 오류 (시간당)
FAILURE_HOUR = 6               # 고장은 06시에 기록
P_SPIKE = 0.005
SPIKE_SIGMA = 4.0


@dataclass
class MachineSim:
    machine_id: int
    state: str = "HEALTHY"     # HEALTHY / DEGRADING / FAULT
    signal: str | None = None
    direction: int = 0
    drift_sigma: float = 0.0
    lead_hours: int = 0
    elapsed: int = 0
    errors_emitted: int = 0


def _pick_error(signal: str | None, rng: random.Random) -> str:
    if signal and signal in SIGNAL_TO_ERROR and rng.random() < SAME_ERROR_PROB:
        return SIGNAL_TO_ERROR[signal]
    return rng.choices(ERROR_IDS, weights=ERROR_WEIGHTS)[0]


def step(m: MachineSim, rng: random.Random, hour_of_day: int) -> dict:
    """시뮬레이션 1시간을 진행한다. m을 직접 갱신하고 이번 시간의 결과를 돌려준다."""
    offsets: dict[str, float] = {}
    errors: list[str] = []
    failure = maint = None

    if m.state == "HEALTHY":
        if rng.random() < P_EPISODE:
            m.state = "DEGRADING"
            m.signal = rng.choice(SIGNALS)
            m.direction = 1 if rng.random() < UP_PROBABILITY else -1
            if rng.random() < STRONG_FRACTION:
                m.drift_sigma = rng.uniform(*STRONG_RANGE)
            else:
                m.drift_sigma = min(max(rng.gauss(DRIFT_MEAN, DRIFT_STD), DRIFT_CLIP[0]), DRIFT_CLIP[1])
            m.lead_hours = rng.randint(*LEAD_HOURS)
            m.elapsed = 0
            m.errors_emitted = 0
    else:
        m.elapsed += 1
        offsets[m.signal] = m.drift_sigma * m.direction * min(m.elapsed / RAMP_UP_HOURS, 1.0)

        if rng.random() < P_PRECURSOR_ERROR:
            errors.append(_pick_error(m.signal, rng))
            m.errors_emitted += 1
            m.state = "FAULT"

        if m.elapsed >= m.lead_hours and hour_of_day == FAILURE_HOUR:
            failure = maint = SIGNAL_TO_COMPONENT.get(m.signal)
            m.state = "HEALTHY"
            m.signal = None
            m.direction = 0
            m.drift_sigma = 0.0
            m.elapsed = 0

    if rng.random() < P_BG_ERROR:
        errors.append(_pick_error(None, rng))

    if rng.random() < P_SPIKE:
        spike_signal = rng.choice(SIGNALS)
        offsets[spike_signal] = offsets.get(spike_signal, 0.0) + SPIKE_SIGMA * rng.choice([-1, 1])

    return {"offsets": offsets, "errors": errors, "failure": failure, "maint": maint}
