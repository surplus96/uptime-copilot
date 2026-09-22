# 자동 감지 시뮬레이터 계획 (브랜치 `workspace-mac-2`)

> 런타임 테스트 흐름 중 "이벤트 생성 → 스캔 → Slack 알림" 구간을 실제 제조 시스템의
> 알람 구조에 맞게 자동화한다. 이후 구간(에이전트 탭 조회 → 사람 승인 → CMMS 등록)은
> 사람이 하는 단계로 그대로 둔다. 이 문서는 설계 기록이며, 구현 상태는 맨 아래
> "진행 상태"에 갱신한다.

## 현재 흐름과 목표 흐름

| 단계 | 현재 | 목표 |
|---|---|---|
| 이벤트 생성 | FastAPI docs에서 `POST /simulate/tick` 수동 호출 | 백그라운드 루프가 자동 생성 |
| 이벤트 확인 | 이상감지 탭의 스캔 버튼 수동 클릭 | 틱마다 자동 스캔 + 탭 자동 갱신 |
| Slack 알림 | 스캔 시 발송 | 스캔당 새 감지 건을 한 메시지로 묶어 발송 |
| 조회·승인·CMMS | 사람 | 그대로 |

## 현재 `generate_tick()`의 한계 (`backend/data/event_simulator.py`)

- **상태 없음**: 호출마다 5% 확률로 이상 설비를 새로 뽑아서 열화가 이어지지 않는다.
- **계단식 이상**: 이상이 걸리면 배치 전체에서 4~6σ 편차가 고정된다(점진적 열화 아님).
- **오류 폭주**: 이상 설비는 시간마다 오류 이벤트를 만든다.
- **수동 트리거**: 틱과 스캔을 각각 사람이 실행한다.
- **비용**: `detect_anomaly()`가 스캔마다 설비의 전체 텔레메트리를 읽고, 시뮬레이터도
  틱마다 전체 테이블로 기준선을 다시 계산한다.

## 확정된 결정

| 항목 | 결정 |
|---|---|
| 시간 압축 | 실제 1분 = 시뮬레이션 1시간 (설정값으로 조정 가능하게) |
| 데이터 분리 | 원본 테이블과 **별도 시뮬레이션 테이블** 사용 |
| 실행 방식 | 단독 서버(워커 1개). 루프 상태는 DB에 저장해서 재시작 시 이어서 진행 |
| UI | 이벤트 목록 자동 갱신 + **시뮬레이터 상태 패널**(`GET /simulator/status` 시각화) |

## 실측 근거 (원본 데이터셋 분석, 2026-09-22)

처음 설계(선형으로 6σ까지 열화, 오류는 FAULT에서만 발생, FAULT 후 거의 확실히 고장)를
원본 데이터(설비 100대, 고장 761건, 오류 3,919건)와 대조해서 다음 수치로 교체했다.

| 측정 항목 | 실제 데이터 | 시뮬레이션 반영 |
|---|---|---|
| 고장 직전 24h, 고장 부품에 대응하는 신호의 \|z\| | 평균 1.6σ (p50 1.55, p90 2.1, p95 2.3). 3σ 이상은 **1.2%** | 열화 크기 ~ N(1.6, 0.4)σ, 0.8~2.6σ로 제한 |
| 대응하지 않는 신호의 편차 | 0.26σ (평상시 0.2σ) | 대응 신호 하나만 편차를 줌 |
| 편차의 시간 모양 | 고장 약 48h 전부터 거의 계단식(72h 전은 평상시 수준) | 리드 44~52h, 6h 램프업 후 고정 |
| 편차 방향 | 양(+)이 66% | 양 방향 확률 0.66 |
| 고장 빈도 | 설비당 연 7.6회 | 열화 시작 확률 0.00087/시간 (= 7.6 ÷ 8760) |
| 고장 직전 168h 내 오류 유무 | 98% (임의 시점 기준선 51.5%) | 열화 중 시간당 5% 오류 발생(에피소드당 평균 2.4건) |
| 고장 부품과 같은 번호 오류 | 64% | 전조 오류 종류를 64% 확률로 대응 오류로 선택 |
| 오류 빈도 | 설비당 연 39.2회 | 고장과 무관한 배경 오류 시간당 0.0022 |
| 오류 종류 분포 | error1~5 = .26/.25/.21/.19/.09 | 같은 가중치로 선택 |
| 고장 시각 | 743/761건이 06시 | 열화 종료 시점을 06시로 정렬 |
| 정비 기록 | 고장과 같은 시각 100% | 고장과 같은 시각에 정비 기록 |

**알려진 편차(독립 확률로는 못 맞춤)**: 실제 데이터는 "오류가 이후 168h 내 고장으로
이어지는 비율"이 35.5%인데 시뮬레이션은 약 56%다. 실제 오류는 시간적으로 뭉쳐서
발생하는데 시뮬레이션은 독립 포아송이기 때문이다. 고장 전 오류 유무(98%)를 우선해서 맞췄다.

**데모용 예외**: 실제로는 3σ 이상 열화가 1.2%뿐이라 텔레메트리 기반 **주의** 감지가 거의
발생하지 않는다. 데모에서 주의 경로가 보이도록 `STRONG_FRACTION`(기본 0.15)만큼은
3.2~4.5σ의 강한 열화로 만든다. 실제 수치보다 높게 잡은 값이며, 0으로 두면 실제 분포다.

## 설비 상태 머신

```
HEALTHY → DEGRADING → FAULT → (고장 + 정비, 06시) → HEALTHY
```

| 상태 | 동작 |
|---|---|
| HEALTHY | 매 시간 `P_EPISODE` 확률로 DEGRADING 진입. 신호 1개, 방향, 편차 크기, 리드 시간을 확정 |
| DEGRADING | 편차가 6시간에 걸쳐 목표 크기까지 오르고 유지. 매 시간 5% 확률로 전조 오류 발생 |
| FAULT | 전조 오류가 1건 이상 발생한 상태(편차 유지). 리드 시간이 지난 뒤 06시에 고장 + 정비 기록 |

- 고장이 나면 부품을 교체한 것으로 보고 바로 HEALTHY로 복귀한다(실제 데이터의 정비 시차 0h).
- 모든 상태에서 고장과 무관한 **배경 오류**가 발생한다(대부분의 오류는 고장으로 이어지지 않음).
- **일시 스파이크**: 상태와 무관하게 시간당 0.5% 확률로 1시간짜리 +4σ. 24시간 평균에서
  약 0.17σ로 희석되어 감지되면 안 되는 오탐 검증용이다(데이터 근거가 아닌 강건성 테스트용).
- 난수는 `random.Random(seed)` 인스턴스를 쓴다(`SIM_SEED`로 재현).
- 이 구조에서 **긴급**은 고장 기록 직후(스캔의 `within_days=1`) 잡히고, **주의**는 강한 열화일 때만
  잡힌다. 약한 열화는 텔레메트리로 안 잡히고 오류 코드로만 드러난다(실제와 같은 난이도).

검증(스크래치에서 2년 × 100대 시뮬레이션, seed 고정): 고장 설비당 연 7.2회(실제 7.6),
고장 시각 전부 06시, 오류 종류 분포와 방향 비율이 실제와 일치, 고장 전 168h 내
오류 96%(실제 98%).

## 테이블

원본(`telemetry`, `errors`, `failures`, `maint`)은 읽기 전용으로 두고, 같은 컬럼 구조의
시뮬레이션 테이블을 추가한다.

- `sim_telemetry(datetime, machineID, volt, rotate, pressure, vibration)`
- `sim_errors(datetime, machineID, errorID)`
- `sim_failures(datetime, machineID, failure)`
- `sim_maint(datetime, machineID, comp)`
- `sim_state(machine_id PK, state, signal, direction, drift_sigma, lead_hours, elapsed, errors_emitted)`
- `sim_control(key PK, value)` — `running`, `sim_now`, `hours_per_tick`

각 시뮬레이션 테이블에는 `(machineID, datetime)` 인덱스를 둔다. 시뮬레이션 초기화는
이 테이블들을 비우는 것으로 끝난다.

## 원본 + 시뮬레이션 통합 조회 (누락되면 에이전트 탭이 시뮬레이션 이벤트를 못 봄)

| 대상 | 변경 |
|---|---|
| `pdm_telemetry.detect_anomaly()` | 기준선은 원본에서만 계산해 캐시, 최근 구간은 `sim_telemetry` 우선 |
| `pdm_operations.get_recent_errors()` | 원본 + `sim_errors` 합쳐서 조회 |
| `pdm_operations.check_recent_failure()` | 원본 + `sim_failures` 합쳐서 조회 |
| `pdm_operations.estimate_next_maintenance()` | 원본 + `sim_maint` 고려 |
| `event_store._dataset_now()` | 원본과 시뮬레이션 텔레메트리 중 최댓값 |
| 기존 `POST /simulate/tick` | 새 시뮬레이션 테이블에 쓰도록 통합 |

참고: 현재 원본 `telemetry`에는 예전 수동 틱 데이터 200행(2016-01-01 07:00~08:00)이
이미 섞여 있다(876,300행 vs 원본 876,100행). 소량이라 그대로 두되, 기준선 계산 시
알고 있어야 한다.

## 백그라운드 루프와 API

- FastAPI lifespan에서 asyncio 태스크를 띄워 `SIM_TICK_SECONDS`(기본 60)마다
  `SIM_HOURS_PER_TICK`(기본 1)시간을 진행한다. 상태는 `sim_control`/`sim_state`에 저장.
- 틱 직후 **증분 스캔**: HEALTHY가 아닌 설비 + 이번 틱에 이벤트가 생긴 설비만 진단.
  기존 `evidence_at` 중복 방지 로직을 그대로 사용한다.
- Slack은 스캔 1회에 새로 감지된 건을 한 메시지로 묶는다(기존 건별 발송 + 1초 sleep 대체).

| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/simulator/start` | 루프 시작 (현재 `sim_state` 그대로 이어감 — 자동으로 지우지 않음) |
| POST | `/simulator/stop` | 루프 정지 |
| GET | `/simulator/status` | 실행 여부, 시뮬레이션 시각, 속도, 열화 진행 목록, `has_stale_events` |
| POST | `/simulator/inject` | 특정 설비를 강제 열화(데모용) |
| POST | `/simulator/reset` | 아래 "클린 상태 보장" 참고 |

## 클린 상태 보장 (2026-09-22 추가)

**발견한 문제**: 시뮬레이션은 "고장 설비 없는 클린 상태에서 시작"을 전제로 설계됐는데
(1번 단계의 `MachineSim`은 전부 HEALTHY로 시작), 실제로는 이전 수동 테스트로 생긴
잔재 데이터가 이미 존재한다 — `event_store.detected_events`에 실제로 3건
(`설비 #5 주의`, `#34/#84 긴급`, 2026-09-18 수동 `/scan` 테스트로 생성)이 남아 있는 걸
직접 확인했다. 자동 시뮬레이터가 새로 돌기 시작해도 이 이벤트들은 사라지지 않고
이상감지 탭에 계속 떠서, 시뮬레이터가 감지한 것처럼 보이는 혼선이 생긴다.

**결정(2026-09-22)**: 자동 리셋은 하지 않는다. `/simulator/start`는 항상 현재
`sim_state`를 그대로 이어간다(서버 재시작 후 진행 유지라는 기존 설계와 일관됨).
잔재 정리는 사용자가 `/simulator/reset`을 **명시적으로 호출**해야만 일어난다 —
자동 삭제로 인한 데이터 유실을 피하기 위함.

**`/simulator/reset`이 지우는 범위** (전부, 부분 리셋 없음):
- `sim_state` → 100대 전부 HEALTHY로
- `sim_telemetry` / `sim_errors` / `sim_failures` / `sim_maint` → 전부 삭제
- `event_store.detected_events` / `completed_events` → 전부 삭제 (원본 여부와 무관하게
  "현재 열려 있는 알람" 전체를 지운다 — 부분적으로 "시뮬레이터가 만든 것만" 구분해서
  지우는 기능은 만들지 않는다. `detected_events`/`completed_events`에는 이벤트가
  어디서 발생했는지 구분할 컬럼이 없어서, 구분하려면 스키마 변경이 필요하고 이번
  스코프에서는 그 비용이 정당화되지 않는다고 판단)
- 원본 `telemetry`/`errors`/`failures`/`maint`는 건드리지 않는다(읽기 전용 유지).

**`GET /simulator/status`에 `has_stale_events` 필드 추가**: `sim_state`가 전부
HEALTHY인데 `detected_events`에 행이 있으면 `true` — "지금 보이는 이벤트는 시뮬레이터가
만든 게 아니라 이전 데이터"라는 신호. UI가 이 값을 보고 "리셋 후 시작하시겠습니까?"
안내를 띄울 수 있다(UI 단계에서 구현).

**README/운영 절차**: 시뮬레이터를 처음 켜기 전에 `/simulator/reset`을 한 번 호출하는
것을 표준 절차로 문서화한다(README 또는 이 파일에 "시작하기 전" 체크리스트로).

## UI

- 이상감지 탭: 이벤트 목록 주기적 자동 갱신(스캔 버튼은 보조로 유지).
- **시뮬레이터 상태 패널**(이상감지 탭 상단):
  - 요약: 실행 중/정지, 시뮬레이션 시각, 속도, 시작/정지 버튼
  - 열화 진행 목록: 설비 번호, 신호, 방향, 상태, 진행률(경과 시간 ÷ 리드 시간), 현재 편차(σ), 발생한 전조 오류 수
  - 이벤트가 뜨기 전 열화 단계부터 보이게 한다.

## 성능

- 설비별 기준선(평균, 표준편차)을 원본에서 1회 계산해 저장·재사용(열화 데이터의
  기준선 오염 방지).
- `detect_anomaly()`가 전체 이력이 아니라 최근 구간만 읽도록 변경.
- 1분 = 1시간이면 하루 약 14만 행이 쌓이므로 `sim_telemetry` 보관 기간 제한(예: 최근 N일)을 둔다.

## 테스트 계획

- 상태 머신은 seed 고정으로 결정론적 단위 테스트(전이 순서, 고장은 06시에만 발생, 전조 오류 후 FAULT 전이, 스파이크 미감지).
- 통합 조회: 시뮬레이션 오류/고장이 `_diagnose_machine`에 반영되는지.
- 각 버그 수정과 같은 방식으로, 수정 전 코드로 되돌렸을 때 테스트가 실패하는지 확인.

## 구현 순서

1. 시뮬레이션 테이블 + 열화 상태 머신 (순수 함수)
2. 기준선 캐시, 인덱스, 원본·시뮬레이션 통합 조회
3. 백그라운드 루프 + 제어 API (`/simulator/reset`의 클린 상태 보장 포함)
4. 증분 자동 스캔 + Slack 묶음 알림
5. UI (이벤트 자동 갱신 + 상태 패널)

## 진행 상태

- [x] 1 — 완료(2026-09-22): `backend/data/sim_engine.py`(상태 머신), `backend/data/sim_store.py`(테이블·상태 저장).
  2년×100대 시뮬레이션과 임시 DB 왕복 검증 통과. 단위 테스트는 아직 없음.
  - 주의: 서버 재시작 후에도 재현되려면 난수 상태를 저장해야 한다(3번 단계에서
    `seed + 진행한 틱 수`로 `Random`을 다시 만드는 방식을 검토).
- [x] 2 — 완료(2026-09-22): `backend/data/sim_query.py`(신규, 원본+sim 통합 조회 공용 헬퍼),
  `pdm_telemetry.detect_anomaly()`(기준선 캐시 + 윈도우 조회), `pdm_operations.py`의
  `get_recent_errors`/`check_recent_failure`/`estimate_next_maintenance`,
  `event_store._dataset_now()`. 원본만 있을 때/`sim_*` 테이블이 비어있을 때/실제 sim 데이터가
  섞였을 때 세 경우 모두 복제 DB로 검증(기준선이 sim 데이터에 오염되지 않는 것 포함),
  기존 24개 테스트 전부 통과, 실제 DB 해시 불변 확인.
  - **동작 변경 주의**: `detect_anomaly()`의 기준선이 이제 원본 전체(과거엔 최근 24시간
    제외)라서, 시뮬레이터를 한 번도 안 돌린 상태에서의 Z-score가 이전과 미세하게 다를 수
    있다. 지금 `detected_events`에 남아있는 3건(#5 주의, #34/#84 긴급)은 이 변경 전
    계산값이라, 리셋 후 재스캔 시 재현 여부가 달라질 수 있다.
- [x] 3 — 완료(2026-09-22): `agent_service.scan_all_machines()`를 `scan_machines(ids)` +
  얇은 래퍼로 리팩터링(동작 불변), 신규 `backend/data/sim_loop.py`(백그라운드 루프,
  `sim_control`에 난수 상태 영속화, `/simulator/*` 5개 엔드포인트), `main.py` lifespan에
  `asyncio.create_task` 연결. 복제 DB로 end-to-end 검증: 실제 잔재 이벤트 3건이 reset으로
  지워짐 → `inject(#7)`로 강제 열화 → 30틱 진행 후 FAULT 도달(진행률 0.68, lead_hours
  확인) → 서버 재시작을 흉내낸 rng 재로드가 완전히 동일 → 이어서 82틱째에 자동 스캔이
  `detected_events`에 (#7, 주의)를 실제로 생성. Slack 발송은 샌드박스가 막아 실패 로그만
  남고(`notify.py`의 방어적 실패 처리 설계대로) 루프는 계속 정상 진행. 실제 DB 해시 불변.
- [x] 4 — 3번 단계에서 함께 구현됨(별도 작업 없음). `_tick_once()`의 `scan_targets`
  계산(HEALTHY 아닌 설비 + 이번 틱에 이벤트가 생긴 설비)과 `agent_service.scan_machines()`
  호출, 새로 감지된 건을 `[자동 스캔] ... N건`으로 묶어 보내는 `notify.send_alert()` 호출이
  전부 `sim_loop.py`에 이미 들어있고, end-to-end 검증(#7 → 82틱째 자동 감지)으로 확인됨.
  2026-09-22 리네이밍: `main.py`의 `/scan` 핸들러가 `agent_service.scan_machines(ids)`와
  이름이 겹쳐 헷갈렸던 것을 `trigger_scan()`으로 정정(호출부 없어 단독 수정, 전체 테스트
  24개 재통과 확인).
- [x] 5 — 완료(2026-09-22): `frontend/streamlit_app.py`에 `_simulator_panel()`
  (`@st.fragment(run_every="10s")`) 추가 - 실행/정지, 시뮬레이션 시각/속도, 열화 진행
  목록(진행률 바), `has_stale_events` 경고 + 리셋 버튼. 이벤트 목록은 기존 렌더링 코드를
  건드리지 않고, 개수 변화를 감지했을 때만 `st.rerun()`(기본 scope="app")으로 전체 갱신.
  Docker 재빌드 후 실제 컨테이너로 end-to-end 검증: reset → inject(#42) → start →
  약 50초 후 첫 틱이 자동 실행되어 `sim_now` 06:00→07:00 전진, #42 진행률 0.0→0.02 —
  전부 실제 백그라운드 루프가 돈 것. 검증 후 stop+reset으로 정리, 실 데이터는 그대로.

## 버그 수정: 시뮬레이션 시각이 원본 데이터셋 끝(2016-01-01)에서 이어받던 문제 (2026-09-22)

**증상 (사용자 실브라우저 테스트로 발견)**: "지금 전체 스캔하기"를 누르면 실시간 시뮬레이터
탭엔 "열화 진행 중인 설비 없음"인데도 긴급 이벤트가 여러 건 떴다.

**원인 (컨테이너 실 DB로 직접 확인)**: `#15/#64/#90/#95`로 뜬 긴급 이벤트는 시뮬레이터가
만든 게 아니라 **원본 정적 데이터셋에 원래부터 있던 2015-12-31 고장 기록**이었다.
`_tick_once()`가 첫 틱의 기준 시각을 `sim_query.dataset_now()`(원본+시뮬레이션 통합
최신 시각)로 잡다 보니, 시뮬레이터의 "지금"이 원본 데이터셋 끝(2016-01-01) 바로
근처에서 시작 - `check_recent_failure(within_days=1)`이 그 근처에 파묻혀 있던 원본의
오래된 고장 기록들을 "방금 발생"으로 오인식해서 되살아났다. 사용자가 제안한 "시뮬레이션
시각을 원본 데이터셋 끝이 아니라 시작 시점의 실제 현재 시각으로" 라는 방향이 정확히
근본 원인을 짚었고, 그대로 적용하니 두 증상(옛날 이벤트 오탐 + 헷갈리는 2016년 시각
표시)이 한 번에 해결됨.

**수정**: `sim_query.py`에 `sim_only_now()`(시뮬레이션 데이터에만 국한된 최신 시각,
원본과 통합 안 함) 추가. `_tick_once()`의 기준 시각과 `status()`의 `sim_now` 표시를
`dataset_now()`(통합) 대신 이걸로 교체 - 리셋 후 첫 틱은 실제 현재 시각(시 단위 반올림)에서
시작하고, 이후 틱은 거기서부터 시간 단위로 전진. `event_store._dataset_now()`(완료
이벤트 재표면화 판단 등)는 통합 방식 그대로 유지 - 시뮬레이터가 실제 "오늘" 기준으로
도는 이상 더는 옛날 기록과 헷갈릴 일이 없음.

**검증**: 재빌드 후 실제 컨테이너로 reset → start → inject(#77) → 8회 폴링(총 2분).
`sim_now`이 2026-09-22 → 09-27로 실제 날짜대로 전진, 설비 여러 대(31/46/62/77/2/48)가
자연 발생으로 DEGRADING→FAULT→HEALTHY 순환. `/scan` 결과 4건 전부 `evidence_at`이
`2026-09-25...`(진짜 시뮬레이션 근거)이고, 원본 2015~2016년 고장 기록은 "최근 오류
이력" 참고 텍스트에만 섞여 나올 뿐 긴급/주의 판정에는 더 이상 관여하지 않음을 확인.

## UI 개선 (2026-09-22, 실브라우저 피드백 반영)

- 리셋 버튼이 `has_stale_events`일 때만 보이던 설계 실수 수정 - 항상 보이는 위치로 이동.
- `sim_control`에 `started_at`/`last_tick_at` 저장 → 상태 패널에 "실행 경과 N분 N초",
  "다음 틱까지 약 N초" 표시 추가(10초마다 패널이 다시 그려지는 것만으로는 "살아있다"는
  느낌이 부족하다는 피드백).
- 속도: 실측 확률(`P_EPISODE` 등)은 그대로 두고, `.env`의 `SIM_TICK_SECONDS=15`/
  `SIM_HOURS_PER_TICK=12`로 압축 비율만 조정(기본값 60/1로는 설비 100대 기준 첫 열화
  시작까지 평균 11분 - 관찰하기엔 너무 느림). 확률 자체를 안 건드려서 통계적 보정은
  안 깨짐.

## 남은 선택 항목 (이번 5단계 범위 밖)

- ~~`/simulate/tick`(수동, 원본 테이블 직접 기록)과 새 자동 루프(`sim_*` 테이블)를
  통합할지는 보류 상태.~~ **2026-09-22 해소**: 통합이 아니라 **삭제**로 결정
  (code-quality-reviewer M6 — 아무도 안 부르는 경로인데, 원본 데이터가 "안 바뀐다"는
  이번 세션의 핵심 전제와 계속 충돌했음). `backend/data/event_simulator.py` 삭제,
  `POST /simulate/tick` 라우트 제거, README(EN/KOR) 엔드포인트 표에서도 제거.
  `pdm_telemetry.get_recent_telemetry()`(호출자 0, L3)도 같이 삭제.
- ~~README에 "시뮬레이터를 켜기 전에 `/simulator/reset` 먼저 호출" 같은 운영 절차
  문서화는 아직 안 함.~~ **2026-09-22 해소**: README(EN/KOR) 둘 다 `/simulator/*` 5개
  엔드포인트 표에 반영, `reset`의 설명에 "새로 시작하기 전에 호출 - 자동으로는 안
  지워짐" 명시. Architecture Principles에도 자동 시뮬레이터 문단 추가.
- ~~`SIM_TICK_SECONDS`/`SIM_HOURS_PER_TICK`/`SIM_SEED` 환경변수는 README 환경변수 표에
  아직 없음.~~ **2026-09-22 해소**: 세 변수 모두 README(EN/KOR) 환경변수 표에 추가.

## 배포 전 전체 점검 (2026-09-22, 5개 전담 에이전트 병렬 실행)

보안·코드품질·빌드·테스트·인터페이스 5개 관점에서 이번 세션의 시뮬레이터 변경분
(커밋 `9b34e21`)을 병렬 점검. 발견 사항이 많아 여기서는 실제로 반영한 것만 요약하고,
전체 findings는 세션 로그 참고.

**즉시 반영 (커밋 `5f30330`)**:
- `.env`가 Docker 이미지에 그대로 구워지고 있던 것 확인(보안+빌드 두 에이전트가
  독립적으로 발견, 실제 컨테이너에 `/app/.env` 존재까지 확인) → `backend/.dockerignore`/
  `frontend/.dockerignore`에 `.env` 제외 추가.
- `conftest.py`의 `event_store_module` fixture가 `sim_query.DB_PATH`는 안 바꿔치기해서,
  `event_store._dataset_now()`가 `sim_query`에 위임하도록 바뀐 뒤로 테스트가 진짜
  운영 DB(67MB)를 읽고 있었음(테스트 에이전트 발견) - 그 결과 2026-09-17에 잡았던
  `evidence_at`/`completed_at` 혼동 회귀를 다시 못 잡는 상태로 조용히 퇴행. 1줄 수정.
- `backend/tests/test_sim_query.py` 신설(25개) - `sim_query.<이름>` 호출부를 AST로
  전수 스캔해서 이름이 실제로 존재하는지 확인하는 테스트 포함, 이게 있었으면
  `sim_only_now()` 삭제 사고를 즉시 잡았을 것(실제로 재현해서 검증함).

**즉시 반영 (2번째 커밋, 보안 HIGH)**:
- `/simulator/inject`에 존재하지 않는 signal 문자열을 넣으면 다음 틱에서 `KeyError`가
  나고, 그 틱 전체가 커밋 전에 죽어서 같은 상태·같은 난수로 무한 반복되는 문제(보안
  에이전트가 실제로 `KeyError` 발생시켜서 재현) → `main.py`의 `SimulatorInjectRequest`에
  `Literal["volt","rotate","pressure","vibration"]` 제약 추가(422로 원천 차단) +
  `sim_engine.py`의 두 dict 조회를 `.get()`으로 방어(2차 방어선).
- `run_forever()`의 `is_running()`이 `try` 바깥에 있어서 DB 오류 한 번에 루프가
  조용히 영구 정지할 수 있던 문제(코드품질+테스트 에이전트 둘 다 지적) → `try` 안으로
  이동, `last_error`/`consecutive_failures`를 `sim_control`에 기록해서 `/simulator/status`
  응답에 노출.
- `_tick_once()`가 이벤트 루프에서 동기 실행되어 틱 도는 동안 `/health`까지 멈추던
  문제(코드품질 에이전트, 실측은 안 됐으나 근거 확인) → `asyncio.to_thread()`로 오프로드.
- 재빌드 후 실제 컨테이너로 검증: 이상한 signal 값 → 422 확인, 정상 흐름(재시작 →
  주입 → 5회 폴링, 여러 설비 자연 발생 열화/고장 순환) → `last_error: null` 유지 확인.

**즉시 반영 (3번째 커밋, 빌드/인프라)**:
- `backend/Dockerfile`에 `ENV HF_HOME=/opt/hf_cache`, `docker-compose.yml`에 named
  volume `hf_cache:/opt/hf_cache` 추가 - 재빌드마다 임베딩 모델(476MB)을 다시 받던
  문제 해결. **실측**: 볼륨 비운 상태(cold)로 `docker compose up -d --build` **6분
  29초**(그래도 정상적으로 healthy까지 도달 - 예전엔 여기서 타임아웃으로 프론트엔드가
  못 떴었음) → 캐시 채워진 상태(warm)로 재빌드 **4.45초**.
- `HF_HUB_DISABLE_XET=1` 추가 - xet(`cas-bridge.xethub.hf.co`) 경로가 이 환경
  프록시와 안 맞아 멈추던 문제. 로그로 확인: 이제 일반 CDN 경로(`us.aws.cdn.hf.co`)로
  나감.
- healthcheck `start_period`를 180s → 600s로, `start_interval: 5s` 추가(Compose
  v5.1.1 확인 후 적용, 2.20.2+ 필요) - 캐시 없는 최초 1회만 여유를 주고 그 외엔
  즉시 healthy로 넘어감. `stop_grace_period: 30s` 추가.
- `main.py`의 lifespan 종료 처리를 `try/finally` + `asyncio.wait_for(timeout=5)`로
  강화 - yield에서 예외가 나도 `sim_task`가 정리되고, 종료 대기가 무제한이 아니게 됨
  (`stop_grace_period`와 짝).
- `./archive` 마운트를 `:ro`로 변경(앱이 읽기만 함).
- `pdm_dataloader.py` 자동 미실행 문제는 README에 이미 문서화돼 있음을 확인
  (`docker compose exec backend python data/pdm_dataloader.py`) - 추가 조치 불필요.

**즉시 반영 (5번째 커밋, 인터페이스)**: 리셋 확인 체크박스 + 삭제 범위 명시, 시작/정지/
리셋 에러 처리(`raise_for_status` + `st.error`), 오인시키던 "이전 데이터" 경고를 중립
문구로 정정, 진행률 바를 한글 라벨+퍼센트로, `last_error`/`consecutive_failures`를
빨간 배너로 노출, 목록 8개로 제한.

**즉시 반영 (6번째 커밋)**:
- `_tick_once()`를 커넥션 하나·트랜잭션 하나로 통합 - 텔레메트리 기록 후 상태 저장
  직전에 죽으면 다음 재시작 때 그 시간이 중복 진행되던 불일치 제거
  (code-quality-reviewer M2). `sim_store.save_states()`에 선택적 `conn` 인자 추가해서
  호출자가 트랜잭션을 공유할 수 있게 함(기존 호출부는 그대로 동작).
- `threading.Lock()`으로 `_tick_once()`/`inject()`/`reset()`이 동시에 `sim_state`를
  건드리지 못하게 함 - `inject()`로 강제 열화시켜도 마침 그때 도는 틱이 조용히
  되돌려놓을 수 있던 문제(security-reviewer M1) 해결. 실제로 틱 도는 중에
  inject 호출해서 유실 안 되는 것까지 확인.
- INSERT 문에 컬럼명을 전부 명시(code-quality-reviewer M7) - 스키마 순서가 바뀌어도
  조용히 밀리지 않도록.
- `DB_PATH` 8곳 통합은 기존 테스트의 `monkeypatch.setattr(module, "DB_PATH", ...)`
  패턴과 얽혀 있고 위 항목들보다 리스크 대비 실익이 낮다고 판단해 보류.

**즉시 반영 (7번째 커밋)**: `/simulate/tick` 라우트, `backend/data/event_simulator.py`,
`pdm_telemetry.get_recent_telemetry()`(호출자 0) 전부 삭제. README(EN/KOR) 엔드포인트
표에서도 제거. 사용자 승인 하에 진행(기존 기능 제거라 별도 확인 거침).

**즉시 반영 (8번째 커밋, lint/타입체커/CI)**: 사용자 승인 하에 진행.
- `backend/pyproject.toml` 신설: ruff(E/F/I 규칙) + mypy + pytest 설정.
- mypy 범위는 `data/` 전체 + `cmms_client.py`(둘 다 이번 세션에 새로 만들거나 많이
  건드린 코드) - 실제로 돌려보니 23개 에러가 나왔는데, `sim_engine.py`/`sim_store.py`/
  `sim_loop.py`/`event_store.py`/`cmms_client.py`의 8개는 실제 버그 소지가 있는
  타입 문제라 전부 고침(예: `event_store._dataset_now()`가 `-> str`로 선언돼
  있었지만 실제로는 텔레메트리가 완전히 비어있으면 `None`을 반환할 수 있었음).
  `agent/agent_service.py`의 나머지 13개는 OpenAI SDK 오버로드 타입 정의가
  겹쳐서 나는 노이즈성 에러 위주라, 지금은 손대지 않고 `ignore_errors=true`로
  범위에서 뺐다(주석에 이유와 확장 방법 남겨둠). **최종: mypy 0 에러.**
- ruff는 import 정렬 9건 + 불필요한 f-string 1건을 자동 수정(동작 변화 없음,
  `--fix`로 처리). `rag/generate_docs.py`의 import 순서 위반 1건은 `sys.path.insert`
  뒤에 오는 게 의도된 배치 스크립트라 그대로 둠.
- `.github/workflows/backend-checks.yml` 신설 - push/PR마다 ruff+mypy+pytest(RAG
  제외 빠른 경로) 실행. `OPENAI_API_KEY` 없이 돈다(테스트가 import하는 모듈 중
  OpenAI 클라이언트를 모듈 레벨에서 만드는 파일은 어떤 테스트도 직접 import 안 함).
- 재빌드 + 실제 컨테이너로 정상 흐름(에이전트 쿼리) 재확인 완료.

**아직 미반영 (다음 라운드, P2 - 배포를 막는 수준 아님)**:
- `DB_PATH` 8곳 통합, `sim_loop.py`를 클래스로 전환(파일이 더 커질 경우에만)
- `agent/agent_service.py`의 mypy 범위 확장(OpenAI SDK 타입 정리 필요)
