import random
from dataclasses import dataclass

SIGNALS = ["volt", "rotate", "pressure", "vibration"]
SIGNAL_TO_COMPONENT = {"volt": "comp1", "rotate": "comp2", "pressure": "comp3", "vibration": "comp4"}

ERROR_IDS = ["error1", "error2", "error3", "error4", "error5"]
ERROR_WEIGHTS = [0.26, 0.25, 0.21, 0.19, 0.09]   # 실제 오류 종류 분포

# 열화 시작은 부품 나이에 의존한다(START_MIN_AGE_H, 아래). 설비·시간당 시작 확률 P_EPISODE는 "모든 부품이 열화
# 가능할 때"의 값이 아니라 정상 상태(정기 정비 사건 P_PM_EVENT와 고장으로 나이가 리셋되는 분포)에서 열화 가능한
# 부품의 비율(약 41%)을 곱해도 설비당 연 7.6회(= 761건 / 100대 / 1년)가 되도록 시뮬레이션으로 보정한 값이다
# (0.0022에서 연 7.2회였고 0.0023으로 올려 3년 x 100대에서 연 7.2~7.5회 - tests/test_sim_engine.py 보정 테스트).
P_EPISODE = 0.0023
LEAD_HOURS = (44, 52)          # 열화 시작 ~ 고장까지
RAMP_UP_HOURS = 6              # 편차가 목표 크기까지 오르는 시간
# 열화 편차의 방향과 크기는 신호마다 정해져 있다 - 원본 고장 직전 24시간 평균의 z값(2026-10-01
# 실측): volt +1.28 / rotate -1.46 / pressure +2.10 / vibration +1.80, 방향은 고장의 100%에서
# 같고 다른 신호는 평균 |z| 0.27로 정상이다. 예전엔 방향을 66% 확률로 무작위로 뽑았는데 4개
# 신호를 합친 비율이라 신호별 정보를 잃은 값이었고, 위험도 모델은 이 방향에 반응하므로
# (rotate가 올라가는 열화는 경보 0%) 열화가 주의로 이어지지 않았다.
SIGNAL_DIRECTION = {"volt": 1, "rotate": -1, "pressure": 1, "vibration": 1}
SIGNAL_DRIFT_MEAN = {"volt": 1.28, "rotate": 1.46, "pressure": 2.10, "vibration": 1.80}
DRIFT_STD = 0.3
DRIFT_CLIP = (0.8, 2.6)
STRONG_FRACTION = 0.15         # 데모용: 이 비율은 3σ 이상 강한 열화 (실제는 1.2%)
STRONG_RANGE = (3.2, 4.5)

# ---- 전조 오류: 원본 고장 761건 직전 오류를 직접 집계해 맞춘 "서명"(2026-10-01) ----------------------
# 부품마다 고장 정확히 24시간 전에 정해진 종류의 오류가 나온다(고장의 95~99%에서 그 시각에 서명 오류가
# 전부 있다). 위험도 모델이 학습한 패턴이 이것이라, 예전의 "시간당 5% 무작위 오류"에는 거의 반응하지 않았다.
SIGNATURE_ERRORS = {
    "comp1": ("error1",),
    "comp2": ("error2", "error3"),
    "comp3": ("error4",),
    "comp4": ("error5",),
}
SIGNATURE_LAG_H = 24           # 서명 오류가 나오는 시점(고장까지 남은 시간) - 실제는 거의 항상 정확히 24시간 전
# 서명 없이 고장나는 비율(= 1 - 서명 완비율): comp1 95.3% / comp2 98.8% / comp3 97.7% / comp4 98.3%
# (24~36시간 전 구간에서 서명 오류가 전부 있는 고장의 비율, 2026-10-01 집계). 이런 "조용한 고장"은
# 주의 없이 긴급으로 바로 넘어가는 것이 실제와 같다.
SIGNATURE_SILENT_PROB = {"comp1": 0.047, "comp2": 0.012, "comp3": 0.023, "comp4": 0.017}
# 서명 말고 열화 중에만 더 나는 잡음 오류는 없다: 고장 직전 72시간 오류 1183건 중 서명 약 999건을 빼면 약
# 0.24건/고장인데 이는 배경 오류율 x 72시간(0.226건)과 같다 - 열화와 무관한 오류다.
P_PRECURSOR_ERROR = 0.0
# 고장과 무관한 배경 오류(설비·시간당): 실제 오류 3930건 중 고장 직전 72시간 밖 2747건 = 0.00314
# (설비당 연 27.5회). 예전 값 0.0005는 6분의 1 수준이었다. 종류 분포는 ERROR_WEIGHTS와 같다.
P_BG_ERROR = 0.00314
FAILURE_HOUR = 6               # 고장은 06시에 기록
P_SPIKE = 0.005
SPIKE_SIGMA = 4.0

# ---- 부품 나이(마지막 정비 후 경과시간)와 정비 ------------------------------------------------------
# 위험도 모델은 "마지막 정비 후 경과시간"에 크게 의존하고, 그 피처를 4개 부품 모두에 대해 함께 쓴다(comp1 모델의
# 분할 횟수: 자기 부품 592 / comp2 340 / comp4 166 / comp3 163). 라이브 시뮬레이션에는 정비 기록이 없어서 값이
# 약 9만 시간(학습 범위 최대 약 1만 시간 밖)이었고, 부품별로 나이를 독립으로 뽑았더니 volt·pressure 경보율이
# 77~81%로 떨어졌다(같은 나이로 묶으면 95~96%). 실제 정비 기록(2015년)의 구조를 따른다:
#  1) 정비는 설비 단위의 "사건"이다: 설비당 연 21.6회, 그중 7.0회는 고장과 같은 시각이고 14.6회는 순수 정기 정비다.
#     사건 하나가 부품 1개(정기 72% / 고장 54%) 또는 2개(정기 28% / 고장 46%)를 함께 정비한다(2개일 때 조합은
#     6가지가 고르게). 고장 사건의 두 번째 부품은 고장난 부품이 아닌 다른 부품이다.
#  2) 부품은 일정 나이가 되기 전에는 거의 고장나지 않는다. 고장 직전 나이의 하한(1%~10% 지점): comp1 1080 /
#     comp2 720 / comp3 1080 / comp4 1080시간 - 아래 START_MIN_AGE_H보다 어린 부품은 열화를 시작하지 않는다.
#     (실제 데이터에는 이 나이 아래의 고장이 드물다. 모델이 어린 부품에 약한 게 아니다: 실제 고장 740건을 고장
#      23시간 전에 재생하면 600~900시간 구간 78건을 포함해 99.7%가 경보를 낸다. 어린 부품의 열화가 안 잡힌 것은
#      시뮬레이터가 실제에 없는 입력을 만들었기 때문이다.)
#  3) 시작 시점의 나이는 같은 사건 과정을 거슬러 올라가며 정한다(draw_initial_ages) - 부품 나이는 "마지막으로 그
#     부품을 포함한 사건 이후 시간"이라 독립이 아니라 같은 사건을 공유한다.
START_MIN_AGE_H = {"comp1": 1080.0, "comp2": 720.0, "comp3": 1080.0, "comp4": 1080.0}
COMPONENTS = ["comp1", "comp2", "comp3", "comp4"]
P_PM_EVENT = 0.001668            # 설비·시간당 순수 정기 정비 사건 확률 (연 14.61회 / 8760)
PM_TWO_COMP_PROB = 0.28          # 정기 정비 사건이 부품 2개를 함께 정비할 확률
FAILURE_EXTRA_COMP_PROB = 0.456  # 고장 사건이 고장난 부품 말고 다른 부품 하나도 같이 정비할 확률
ANY_EVENT_RATE = 0.00247         # 설비·시간당 모든 정비 사건(정기+고장) - 시작 나이를 거슬러 올라가며 정할 때만 쓴다
ANY_EVENT_TWO_COMP_PROB = 0.334  # 그 사건이 부품 2개를 정비할 확률(전체 723 / 2163)


def draw_initial_ages(rng: random.Random) -> dict[str, float]:
    """설비 하나의 부품별 "마지막 정비 후 경과시간"(시간)을 정비 사건 과정을 거슬러 올라가며 뽑는다 - 지금부터
    과거로 사건 간격을 지수분포(ANY_EVENT_RATE)로 건너뛰며, 사건마다 부품 1~2개를 골라 아직 나이가 없는 부품에
    그 시점까지의 시간을 준다. 4개가 다 정해지면 끝난다(안전장치로 500사건)."""
    ages: dict[str, float] = {}
    t = 0.0
    for _ in range(500):
        t += rng.expovariate(ANY_EVENT_RATE)
        k = 2 if rng.random() < ANY_EVENT_TWO_COMP_PROB else 1
        for comp in rng.sample(COMPONENTS, k):
            ages.setdefault(comp, t)
        if len(ages) == len(COMPONENTS):
            break
    for comp in COMPONENTS:  # 안전장치에 걸린 경우 - 사실상 일어나지 않는다
        ages.setdefault(comp, t)
    return ages


def pick_event_comps(rng: random.Random, two_comp_prob: float, exclude: str | None = None) -> list[str]:
    """정비 사건 하나가 정비하는 부품 목록(1개 또는 2개). exclude는 고장난 부품처럼 이미 정비되는 부품이다."""
    pool = [c for c in COMPONENTS if c != exclude]
    return rng.sample(pool, 2 if rng.random() < two_comp_prob else 1)


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


def _pick_error(rng: random.Random) -> str:
    return rng.choices(ERROR_IDS, weights=ERROR_WEIGHTS)[0]


def hours_until_failure(elapsed: int, lead_hours: int, hour_of_day: int) -> int:
    """지금(elapsed번째 시간, 시각 hour_of_day)부터 고장이 기록되기까지 남은 시간.
    고장은 elapsed가 lead_hours 이상이 된 뒤 처음 오는 06시에 기록되므로(step 참고) 저장된
    상태(elapsed/lead_hours)와 현재 시각만으로 항상 다시 계산할 수 있다 - 서명 시점을 정하려고
    sim_state에 열을 더하면 이미 만들어진 DB를 마이그레이션해야 해서 피한다."""
    first_06 = elapsed + (FAILURE_HOUR - hour_of_day) % 24
    if first_06 < lead_hours:
        first_06 += 24 * -(-(lead_hours - first_06) // 24)
    return first_06 - elapsed


def _is_silent(m: "MachineSim", comp: str) -> bool:
    """이 에피소드가 서명 오류 없이(조용히) 고장나는지. 에피소드마다 고정이고 재현 가능해야 하므로
    (같은 seed -> 같은 결과) 상태에 저장하는 대신 에피소드를 가리키는 값에서 결정한다."""
    return random.Random(f"{m.machine_id}:{m.drift_sigma}:{m.lead_hours}:silent").random() < SIGNATURE_SILENT_PROB[comp]


def step(m: MachineSim, rng: random.Random, hour_of_day: int, comp_ages: dict[str, float] | None = None) -> dict:
    """시뮬레이션 1시간을 진행한다. m을 직접 갱신하고 이번 시간의 결과를 돌려준다.
    comp_ages(부품 -> 마지막 정비 후 경과시간)를 주면 그 부품의 START_MIN_AGE_H보다 어린 부품에는 열화를
    시작하지 않는다. 안 주면(None) 나이와 무관하게 시작한다."""
    offsets: dict[str, float] = {}
    errors: list[str] = []
    failure = maint = None

    if m.state == "HEALTHY":
        if rng.random() < P_EPISODE:
            signal = rng.choice(SIGNALS)
            start_comp = SIGNAL_TO_COMPONENT[signal]
            young = comp_ages is not None and comp_ages.get(start_comp, float("inf")) < START_MIN_AGE_H[start_comp]
            if not young:
                m.state = "DEGRADING"
                m.signal = signal
                m.direction = SIGNAL_DIRECTION[signal]
                if rng.random() < STRONG_FRACTION:
                    m.drift_sigma = rng.uniform(*STRONG_RANGE)
                else:
                    m.drift_sigma = min(max(rng.gauss(SIGNAL_DRIFT_MEAN[signal], DRIFT_STD), DRIFT_CLIP[0]), DRIFT_CLIP[1])
                m.lead_hours = rng.randint(*LEAD_HOURS)
                m.elapsed = 0
                m.errors_emitted = 0
    else:
        assert m.signal is not None, "signal은 HEALTHY가 아닌 상태에서 항상 설정돼 있어야 한다"
        m.elapsed += 1
        offsets[m.signal] = m.drift_sigma * m.direction * min(m.elapsed / RAMP_UP_HOURS, 1.0)

        # 서명 오류: 고장까지 정해진 시간이 남은 시점에 이 부품의 오류 종류를 전부 낸다.
        # (rng를 쓰지 않는다 - 잡음 오류의 난수열이 서명 유무에 따라 달라지지 않게)
        if hours_until_failure(m.elapsed, m.lead_hours, hour_of_day) == SIGNATURE_LAG_H:
            comp = SIGNAL_TO_COMPONENT.get(m.signal)
            if comp and not _is_silent(m, comp):
                errors.extend(SIGNATURE_ERRORS[comp])
                m.errors_emitted += len(SIGNATURE_ERRORS[comp])
                m.state = "FAULT"

        if rng.random() < P_PRECURSOR_ERROR:
            errors.append(_pick_error(rng))
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
        errors.append(_pick_error(rng))

    if rng.random() < P_SPIKE:
        spike_signal = rng.choice(SIGNALS)
        offsets[spike_signal] = offsets.get(spike_signal, 0.0) + SPIKE_SIGMA * rng.choice([-1, 1])

    return {"offsets": offsets, "errors": errors, "failure": failure, "maint": maint}
