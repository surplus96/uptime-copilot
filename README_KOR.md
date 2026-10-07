# Uptime Copilot

[![English](https://img.shields.io/badge/English-switch-555555?style=for-the-badge)](README.md)
[![한국어](https://img.shields.io/badge/한국어-현재-0c7c8c?style=for-the-badge)](README_KOR.md)

[![Backend checks](https://github.com/surplus96/uptime-copilot/actions/workflows/backend-checks.yml/badge.svg)](https://github.com/surplus96/uptime-copilot/actions/workflows/backend-checks.yml)

**Uptime Copilot — Predictive Maintenance & MRO Copilot**

RAG + 멀티에이전트 백엔드(FastAPI/LangGraph)와 Streamlit 프론트엔드로 구성된, 제조 설비
정비 도메인 예제입니다(Azure Predictive Maintenance 데이터셋 기반). MRO(부품·재고·조달)
계층과 폐쇄망 보안 운영(오프라인 모드, 감사 로그)까지 확장했습니다 — 아래 MRO 확장 참고.

> 📄 **[포트폴리오 케이스 스터디 →](docs/PORTFOLIO_KOR.md)** — 아키텍처, 설계 결정, 엔지니어링 사례.

> **한화의 TOMMS/HUMS 공개 자료에서 착안한 독립 프로젝트**입니다 — 한화와 제휴·후원 관계가
> 없으며 한화를 위해 만들어진 것도 아닙니다. 부품번호·재고·리드타임·단가·단종일은 모두
> 합성값입니다. 수요 예측에 쓰는 교체 이력은 공개 Azure PdM 데이터셋의 것이며, 그 데이터셋
> 자체도 시뮬레이션 데이터입니다.

## MRO 확장 (부품·수요예측·재고위험·오프라인 모드·감사 로그)

핵심 파이프라인 위에 부품 마스터, 수요 예측, 재고 위험 대시보드를 얹고 오프라인 모드와
감사 로그로 폐쇄망 배포를 지원합니다. 계획·결정 기록:
[mro-copilot-upgrade-plan.md](mro-copilot-upgrade-plan.md), [docs/decisions.md](docs/decisions.md).

- **부품 마스터** (`backend/data/parts_master.py`) — 합성 부품 5개(comp1~4 + comp4용 단종
  대체품 1개), 기동 시 자동 시드. 부품번호·재고·리드타임·단종일 전부 합성값입니다 —
  [docs/design/parts_assumptions.md](docs/design/parts_assumptions.md) 참고.
- **수요 예측** (`backend/data/demand_forecast.py`) — 부품별 30/90일 교체 수요를 예방정비
  항(계획대로, 모델 무관)과 고장교체 항(오늘은 실제 경보 상태, 미래는 모집단 기본율 —
  이 둘을 바꿔 쓰면 실제로 버그가 났습니다, `docs/decisions.md` 2026-09-28)으로 나눠
  계산합니다. 1~10월 데이터로만 적합해 11~12월 실측 교체 건수와 대조한 백테스트 오차
  **-5.0%~+3.5%**. 이 값은 기본 교체율을 검증할 뿐 경보 항은 아닙니다 — 경보 항 자체는
  ~120건 중 0~2건만 더합니다.
- **재고 위험 탭** — `GET /parts/inventory_risk`. 반올림된 추정치이며 이미 발주해 입고
  대기 중인 물량은 반영하지 않습니다.
- **우선순위는 부품 가용성과 분리** — 부품이 없다고 진단이 더 급해지지 않습니다. 작업지시서의
  별도 `[조달 긴급도]` 줄로만 표시됩니다.
- **오프라인 모드** (`backend/core/offline_guard.py`) — 모듈별 스위치가 아니라 목적지 기반
  허용 목록(`localhost`/`127.0.0.1`/`host.docker.internal`)입니다. `OFFLINE=1`이면 알 수
  없는 값, `LLM_PROVIDER=openai`, 허용 목록 밖 엔드포인트, 모호한 URL, 허용 목록 호스트를
  우회시키는 프록시(`NO_PROXY`로 제외해야 함)에서 기동을 거부하고, 다른 무엇보다 먼저
  `HF_HUB_OFFLINE=1`(임베딩 모델이 이미 캐시에 있어야 함 — 캐시가 빈 채로 `OFFLINE=1` 첫
  실행을 하면 멈추지 않고 기동 시점에 실패함)과 LangSmith 트레이싱 비활성화를 적용하며
  (`main.py` 진입점에 한해 성립), Slack은 스킵, CMMS는 허용 목록 밖이면 거부합니다. 환경변수만
  검사하고 OS 수준 프록시 설정은 검사하지 않습니다. CI(`test_offline_guard.py`)가 실제 소켓을
  막고 LLM과 진단을 스텁으로 둔 진단→승인 시나리오를 돌립니다. **증명하지 못한 것**: 프록시
  경유·DNS·리다이렉트(프록시는 대신 기동 시점에 거부), 기동·RAG 초기화(따로 확인했지만 CI
  대상은 아님).
- **감사 로그** (`backend/data/audit_log.py`) — LLM을 쓰는 모든 단계(실패 포함), 부품
  가용성 조회, 승인/반려, Slack·CMMS 전송을 실제 결과(전송됨/실패/미설정/차단)와 함께
  시각·스레드ID·이벤트유형·결과로 기록합니다. **기록하지 않는 것**: 진단용 데이터 조회와
  백그라운드 스캔의 부품 조회이며, 재시도한 호출과 RAG 답변+판정은 각각 한 행으로 남습니다.
  자유 텍스트는 저장 전 개인정보를 마스킹합니다(주민번호·카드·휴대폰·국제전화·유선전화·
  이메일 — 이름·주소는 불가). `GET /audit_log` 또는 Streamlit 5번째 탭에서 조회.

## 폴더 구조

```
uptime-copilot/
├── archive/               Azure PdM 원본 CSV (직접 다운로드 필요 - "데이터 준비" 참고)
├── backend/                FastAPI 백엔드
│   ├── main.py               진입점 (uvicorn main:app)
│   ├── core/                  Harness(입출력 검증), LLM 제공자 어댑터, 오프라인 가드
│   ├── rag/                    RAG 파이프라인(하이브리드 BM25+Dense 검색) + docs/
│   ├── agent/                  LangGraph 멀티에이전트 그래프(라우팅 + HITL + 이벤트 스캐너)
│   ├── ml/                      고장 위험 모델: 피처 파이프라인, 학습, 예측
│   ├── data/                    PdM 데이터 계층 + 이벤트 스토어, 부품 마스터, 수요 예측,
│   │                             감사 로그, 열화 시뮬레이터(SQLite)
│   ├── store/                    생성되는 상태(gitignore 대상) — Docker 볼륨 마운트 위치
│   ├── tests/                    pytest 회귀 테스트 — "검사 실행" 참고
│   ├── notify.py                 Slack 알림 — 선택 사항, "환경 변수" 참고
│   ├── cmms_client.py             CMMS 작업지시서 전송 — 선택 사항, docs/design/PHASE_7_PLAN.md 참고
│   └── Dockerfile
├── frontend/                Streamlit 채팅 UI
│   └── Dockerfile
├── .github/workflows/       CI: 모든 push/PR마다 ruff + mypy + pytest 실행
└── docker-compose.yml       "Docker Compose로 실행" 참고
```

## 사전 요구사항

- Python 3.12 (CI와 Docker가 쓰는 버전. 의존성이 고정돼 있지 않고 현재 해석은 ≥3.12가
  필요한 numpy 2.x/scipy를 받습니다 — 그 이하는 검증하지 않았습니다)
- Docker + Docker Compose (수동 venv 설정을 건너뛰고 싶은 경우)

## Docker Compose로 실행 (권장)

데이터셋은 어떤 방식이든 직접 다운로드해야 합니다(재배포 불가). 아래 "데이터 준비"는
실행 방식과 관계없이 필요합니다.

```bash
# 1) 데이터 준비(아래 참고) — 먼저 데이터셋을 archive/에 다운로드

# 2) 설정
cp backend/.env.example backend/.env
# backend/.env 채우기 — OpenAI 키는 여기선 필요 없습니다(compose가 기본으로 로컬 Ollama를
# 씁니다). "환경 변수" 참고

# 3) 두 서비스 빌드 및 실행
docker compose up -d --build
```

- 프론트엔드: http://localhost:8501 — 백엔드: http://localhost:8000 (`/docs`에서 API 문서)
- **완전 cold boot는 최대 10분 정도**(헬스체크가 `start_period: 600s`까지 허용): 백엔드가
  컨테이너 안에서 약 470MB 임베딩 모델을 내려받습니다. `docker compose logs -f backend`로
  진행 확인. 이후 `hf_cache` 볼륨에 캐시되어 재빌드 시 몇 초 만에 healthy가 됩니다.
- **최초 1회 데이터 적재**, 컨테이너 안에서 직접(이미지에는 의도적으로 데이터가 없음):
  ```bash
  docker compose exec backend python data/pdm_dataloader.py
  ```
- **고장 위험 모델도 같은 최초 1회 실행 필요** — 안 하면 진단이 더 약한 Z-score 기준선으로
  조용히 대체되고, `/parts/inventory_risk`는 `503`으로 답합니다:
  ```bash
  docker compose exec backend python -m ml.build_features
  docker compose exec backend python -m ml.train
  ```
- 상태(`backend/store/`)는 named volume에 저장되어 `down`/`up` 사이에도 유지됩니다.
  `docker compose down -v`만 삭제합니다(적재·임베딩 다운로드를 다시 해야 함).
- 두 서비스 모두 `127.0.0.1`에만 바인딩 — 아래 Ollama 설정을 제외하면 LAN에 아무것도
  노출하지 않습니다.
- 중지는 `docker compose down`.
- **compose는 백엔드 기본값을 `LLM_PROVIDER=ollama`로 강제**합니다(코드 자체 기본값은
  `openai`; compose의 `environment:` 블록이 `.env`보다 항상 우선 — 트레이드오프는 아래
  평가 기준선 참고). 컨테이너가 접근하려면 호스트에서 `OLLAMA_HOST=0.0.0.0 ollama serve`로
  Ollama를 켜고(기본값인 루프백 바인딩은 컨테이너에서 접근 불가) `ollama pull qwen3:8b`로
  모델을 받으세요. compose가 `OLLAMA_BASE_URL`을 `host.docker.internal`로 이미 맞춰뒀습니다
  (Linux Docker Engine용 `extra_hosts`도 포함, Linux 호스트에서는 검증하지 못함). 클라우드
  모델을 쓰려면 `docker-compose.yml`의 `LLM_PROVIDER: openai`로 바꾸세요.
  **보안 주의:** `OLLAMA_HOST=0.0.0.0`은 인증 없는 API를 모든 네트워크 인터페이스에
  노출합니다 — 방화벽으로 Docker 브리지/루프백만 허용하세요(그렇지 않으면 "오프라인"
  배포 취지와 어긋납니다). Docker Desktop이 루프백에만 바인딩된 Ollama에 대신 접근할 수
  있는지는 여기서 검증하지 못했습니다.
- **권장**: 호스트에서 `OLLAMA_KEEP_ALIVE=30m ollama serve`로 켜두면 유휴 시간 뒤에도
  모델이 메모리에서 바로 내려가지 않습니다(기본 약 5분 유휴 후 첫 요청은 디스크 재로드
  지연을 떠안습니다).

## Docker 없이 실행

```bash
# 백엔드
cd backend
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # Windows: copy .env.example .env
# .env 채우기 (LLM_PROVIDER=ollama가 아니면 OPENAI_API_KEY 필수 - "환경 변수" 참고)
```

### 데이터 준비 (최초 1회, `pip install` 이후 첫 실행 전)

1. [Microsoft Azure Predictive Maintenance 데이터셋](https://www.kaggle.com/datasets/arnabbiswas1/microsoft-azure-predictive-maintenance)을
   다운로드합니다 (PdM_machines.csv, PdM_errors.csv, PdM_maint.csv, PdM_failures.csv,
   PdM_telemetry.csv — 마지막 파일만 약 87만 행이라 적재에 시간이 좀 걸립니다).
2. 파일들을 `uptime-copilot/archive/` 아래에 둡니다.
3. 최초 1회 SQLite 적재 (`backend/`에서, venv 활성화 상태):
   ```bash
   python data/pdm_dataloader.py
   ```
4. 고장 위험 모델 피처 생성·학습(최초 1회 — 안 하면 더 약한 Z-score로 대체되고 경고 로그):
   ```bash
   python -m ml.build_features
   python -m ml.train
   ```

### 첫 실행

```bash
uvicorn main:app --reload --port 8000
```

최초 실행 시 `intfloat/multilingual-e5-small` 임베딩 모델(약 470MB)을 내려받고 RAG
인덱스를 만듭니다 — 몇 분과 네트워크가 필요하며 멈춘 것처럼 보이지만 정상입니다. 이후
재시작은 빠릅니다. 다운로드가 멈추면 uvicorn 실행 전에 `HF_HUB_DISABLE_XET=1`을
설정하세요(일부 네트워크에서 "xet" 전송 경로가 막혀 있습니다. 컨테이너 경로엔 이미 설정됨).

```bash
# 프론트엔드 (별도 터미널)
cd frontend
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run streamlit_app.py
```

## 검사 실행

```bash
cd backend
source .venv/bin/activate
pip install ruff mypy          # requirements.txt엔 없음(개발 전용)
ruff check .                   # 린트 + import 순서
mypy                           # data/ + cmms_client.py 타입 검사, 항상 0 에러 유지
pytest tests/ --ignore=tests/test_rag_dedup.py   # 빠른 경로 - 임베딩 모델 테스트 제외
pytest tests/                                     # 전체 스위트, 임베딩 모델 포함
```

CI(`.github/workflows/backend-checks.yml`)가 모든 push/PR마다 `ruff check`, `mypy`, 빠른
경로 pytest를 실행합니다.

## 평가 기준선

40문항 골든셋(`backend/tests/eval/golden.jsonl`)으로 실제 LLM 호출을 통해 라우팅·설비번호
추출 정확도를 확인합니다. API 토큰 비용이 들어 기본 테스트와 CI에서 제외되어 있습니다:

```bash
cd backend
pytest tests/eval/test_golden.py -m eval -v -s
```

| 지표 | 정확도 | 측정일 |
|---|---|---|
| 라우터 (카테고리) | 90.0% | 2026-09-27 |
| 설비번호 추출 | 100.0% | 2026-09-27 |
| 번호 없을 때 되묻기 | 100.0% | 2026-09-27 |

(클라우드 모델 `gpt-5.6-luna`, 1회 실행. 2026-09-23/25의 이전 실행은 제공자 기록이 없어
인용하지 않습니다.) 실행마다 상세 결과가 `backend/tests/eval/results/<timestamp>.json`에
저장됩니다(gitignore 대상 — 재실행으로 재현).

### 로컬(Ollama) vs 클라우드

| 지표 | 클라우드 (1회) | 로컬 Qwen3-8B (5회) |
|---|---|---|
| 라우터 정확도 (골든셋 40문항) | 90.0% | 87.5~92.5% (평균 91.0%) |
| 라우터 정확도 (홀드아웃 10문항, 튜닝에 안 씀) | 100.0% | 90~100% (평균 96.0%) |
| 설비번호 추출 / 번호 없을 때 되묻기 | 100.0% / 100.0% | 100.0% / 100.0% |
| 위험한 오분류(설비 언급 → 근거없는 노드) | 0 | 0 (10회 전체) |
| 호출당 평균 지연 | 약 1.2초 | 약 13초(메모리 압박 시 최대 ~40초까지 튀는 경우 있음) |

로컬이 이제 클라우드와 같은 범위의 정확도이고, 안전 관련 핵심 지표(위험한 오분류)는
전체 실행에서 0건입니다. 남은 비용은 지연(~10배, 호출 병합과 워밍업으로 완화되나 없어지진
않음)입니다. 전체 방법론과 라우팅 격차를 줄인 방법은 `docs/PORTFOLIO_KOR.md` §09 참고.
지연 수치는 저장된 산출물이 없고, 평가 결과 JSON은 gitignore 대상이라 `pytest -m eval`
재실행으로만 재확인할 수 있습니다.

## 환경 변수 (`backend/.env`)

| 변수 | 필수 | 미설정 시 동작 |
|---|---|---|
| `LLM_PROVIDER` | 선택 | 코드 기본값 `openai`; `ollama`면 완전히 로컬 서버로 동작 — API 키·인터넷 불필요, 정확도/지연 손실 있음("평가 기준선" 참고). **`docker-compose.yml`이 이 값을 `ollama`로 강제**(`.env`보다 우선). |
| `OFFLINE` | 선택 | `1`/`true`/`yes`/`on`이면 오프라인 가드 켜짐(위 MRO 확장 참고), `0`/`false`/`no`/`off`/빈 값이면 꺼짐, 그 외 값은 기동 거부. 켜지면(`main.py` 진입점에 한해 성립): LangSmith 끔, Slack 스킵, `HF_HUB_OFFLINE=1` 강제(임베딩 모델이 이미 캐시에 있어야 함), 허용 목록 밖 CMMS 거부. `LLM_PROVIDER=openai`, 허용 목록 밖 엔드포인트, `NO_PROXY`로 제외 안 된 프록시(환경변수만 검사, OS 수준 설정은 검사 안 함)에서도 기동 거부. |
| `OPENAI_API_KEY` | **`LLM_PROVIDER=ollama`가 아니면 필수** | 백엔드가 시작되지 않음. 도커 경로는 불필요(compose가 `ollama` 설정). |
| `OPENAI_MODEL` | 선택 | 기본값 `gpt-5.6-luna`. `LLM_PROVIDER=openai`일 때만 사용 |
| `OLLAMA_BASE_URL` | 선택 | 기본값 `http://localhost:11434/v1`. `LLM_PROVIDER=ollama`일 때만 사용 |
| `OLLAMA_MODEL` | 선택 | 기본값 `qwen3:8b` — 먼저 `ollama pull qwen3:8b`로 받아야 함 |
| `LANGCHAIN_TRACING_V2` / `LANGCHAIN_API_KEY` / `LANGCHAIN_PROJECT` | 선택 | LangSmith 트레이싱 비활성화 |
| `SLACK_WEBHOOK_URL` | 선택 | 긴급/주의 Slack 알림을 조용히 건너뜀 (`backend/notify.py`) |
| `CMMS_MCP_URL` + `CMMS_MCP_TOKEN` | 선택 | CMMS 작업지시서 전송을 조용히 건너뜀 — 설정 시 Atlas-MCP + Atlas CMMS 인스턴스 필요, `docs/design/PHASE_7_PLAN.md` 참고 |
| `ALLOWED_HOSTS` | 선택 | `localhost`/`127.0.0.1` 외에 `TrustedHostMiddleware`를 통과시킬 추가 호스트. compose가 `backend`로 자동 설정. |
| `BACKEND_URL` (프론트엔드용) | 선택 | Streamlit이 백엔드를 찾는 주소. 기본값 `http://localhost:8000`, compose는 `http://backend:8000`. |
| `SIM_TICK_SECONDS` | 선택 | 기본값 `60` — 시뮬레이터가 실제 몇 초마다 전진할지 |
| `SIM_HOURS_PER_TICK` | 선택 | 기본값 `1` — 한 번에 시뮬레이션 몇 시간을 진행할지 |
| `SIM_SEED` | 선택 | 기본값 `42` — 시뮬레이터 난수 시드. `POST /simulator/reset`으로 재시작 |

> **`backend/.env.example`은 데모용 시뮬레이터 값**(`SIM_TICK_SECONDS=15`,
> `SIM_HOURS_PER_TICK=12`, 코드 기본값 대비 약 48배 빠름)을 씁니다 — 1분 안에 이벤트가
> 뜹니다. 더 느린(실시간에 가까운) 속도를 원하면 이 두 줄을 지우거나 `60`/`1`로 바꾸세요.

> **호스트의 Atlas-MCP를 Docker 백엔드에서 쓸 때:** 컨테이너 안의 `localhost`는 컨테이너
> 자신을 가리킵니다. `CMMS_MCP_URL`에 `http://host.docker.internal:PORT/mcp`를 쓰고,
> Atlas-MCP의 `ALLOWED_HOSTS`에도 `host.docker.internal:PORT`를 추가하세요(안 그러면
> 421로 거부됩니다).

## 주요 API 엔드포인트

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/health` | 헬스 체크 |
| POST | `/rag/query` | RAG 기반 문서 Q&A |
| POST | `/agent/query` | 멀티에이전트 질의 — 긴급 건이면 `pending_approval` 반환 |
| POST | `/agent/resume` | HITL 승인/반려 결정 후 그래프 재개 |
| GET | `/agent/status/{thread_id}` | 클라이언트 타임아웃 뒤 복구 조회: `pending_approval`, `done`, `running`(로컬 모델에서 3분 안팎 정상), `failed`(오류 유형만 반환), `not_found` |
| POST | `/scan` | 100대 설비 전체 스캔(LLM 미사용), 긴급/주의 건을 이벤트 스토어에 저장 |
| GET | `/events` | 대기 중인 감지 이벤트 목록 |
| POST | `/events/complete` | 이벤트 완료 처리 — 진짜 새 근거가 있을 때만 재표면화 |
| POST | `/events/delete` | 기록 없이 이벤트 폐기 (다음 스캔에서 재등장 가능) |
| POST | `/simulator/start` | 자동 열화 시뮬레이터 시작 (현재 상태에서 이어감) |
| POST | `/simulator/stop` | 루프 정지 |
| GET | `/simulator/status` | 실행 여부, 시뮬레이션 시각, 열화 진행 설비, 오류 상태 |
| POST | `/simulator/inject` | 특정 설비를 강제 열화, 데모용 (`{"machine_id": 12}`) |
| POST | `/simulator/reset` | 시뮬레이터+이벤트 상태 초기화 — 새로 시작하기 전에 호출 |
| GET | `/parts/inventory_risk` | 부품별 30/90일 수요 대비 재고, 조달 가능 여부 플래그 |
| GET | `/audit_log` | 감사 로그 조회 (`event_type`·`thread_id`·`since`·`until`, `limit` 1~1000) |

`/agent/query` 요청 예시:
```json
{"message": "Machine #12 has an error, what's wrong?", "thread_id": "unique-thread-id"}
```
응답이 `{"status": "pending_approval", "message": "..."}`이면, 같은 `thread_id`로
`{"thread_id": "...", "approved": true|false}`를 `/agent/resume`에 보내 실행을 이어갑니다.

## 아키텍처 원칙

- **Harness**: 모델 입출력을 검증하는 결정론적 계층(`core/harness.py`) — 정규식 PII
  체크(계산적) + LLM-as-judge 체크(추론적).
- **RAG**: 하이브리드(BM25 + Dense) 검색, 자동 Faithfulness 채점. Multi-Query 재작성과
  Self-RAG 검색 필요성 판단은 2026-09-23에 제거 — 이 코퍼스 규모에서는 측정 가능한 실익이
  없었습니다.
- **Agent**: LangGraph `StateGraph`가 질문을 진단/정비 일정/일반 문의로 라우팅합니다.
  진단은 3단계: 일반(normal) / 주의(caution — 고장 위험 모델 경보, 학습된 모델이 없을
  때만 Z-score로 대체) / 긴급(urgent — 실제 로그된 고장 기록이라 **고장 이후** 단계이며,
  사전 경고는 주의). 긴급만 3관점 병렬 평가 후
  HITL 승인 대기, 주의는 바로 작업지시서로. 승인 상태는 SQLite에 체크포인트되어 재시작에도
  유지됩니다. 별도 `/scan`이 LLM 없이 100대 전체에 같은 진단 로직을 돌리며, 완료된 이벤트는
  진짜 새 근거가 나타나야만 다시 표면화됩니다.
- **자동 시뮬레이터**: `backend/data/sim_engine.py` 등이 설비별 열화 상태 머신(HEALTHY →
  DEGRADING → FAULT → 고장+정비)을 돌리며, 실제 데이터셋에서 측정한 통계로 보정했습니다
  (`docs/design/SIMULATOR_PLAN.md` 참고). 전조 오류와 신호별 열화 방향은 실제 데이터에서 측정한
  고장 직전 패턴을 따릅니다. 오프라인 재생(`python -m ml.sim_alarm_eval --episodes 150 --seed 2`,
  `backend/` 또는 백엔드 컨테이너에서 학습된 모델과 적재된 데이터셋으로 실행, 시뮬레이션 12시간마다 스캔)에서 신호별
  열화의 83~99%가 고장 전에 위험 점수가 주의 기준을 넘고 항상 맞는 부품을 가리킵니다(실제 데이터처럼
  서명 오류가 없는 고장 1~5% 포함). 오류율, 24시간 전 서명 시점, 부품별 나이 하한, 부품이 같은 사건으로
  함께 정비되는 정비 사건 과정을 모두 실제 데이터에서 측정해 맞췄고, 실제 고장을 모델에 재생했을 때의 약 99%(시험 구간인
  11~12월 고장만 99.2%)와의 남은 차이는 실제 데이터의 15일 정비 달력과 텔레메트리 미세 구조를 시뮬레이터가
  재현하지 않기 때문으로 보며, 실제 값을 대입해 일부만 확인했을 뿐 완전히 닫지는 못했습니다. 이는 시뮬레이터가 모델과 일관됨을 보일 뿐(둘 다 같은 데이터셋에서 나왔음) 현장에서 모델이
  경보를 낸다는 뜻은 아닙니다. 전용 `sim_*` 테이블에만 기록하며 완전히 선택 사항입니다(기본값은 정지).
- **Data**: 경로 상수는 작업 디렉터리가 아니라 파일 자신의 위치 기준입니다. 생성되는
  상태는 `backend/store/` 아래에 소스와 분리되어 있어 Docker 볼륨이 코드를 가리지 않습니다.
- **Observability**: LangSmith 연동, 같은 PII 정규식으로 트레이스 전송 전 익명화.

## 관련 문서

- `docs/PORTFOLIO_KOR.md` — 아키텍처/설계 케이스 스터디 (다이어그램, 주요 결정, 사례)
- `docs/design/SIMULATOR_PLAN.md` — 시뮬레이터 설계 기록: 보정 데이터, 상태 머신
- `docs/INTEGRATION_CONTRACT.md` — 실연동 시 공급해야 할 입력(정비 기록, 오류 코드 매핑, 시간 기준 등)과 모델이 말할 수 있는 것/없는 것
- `docs/design/PHASE_7_PLAN.md` — **선택 사항, 실행에 필수 아님.** Slack + CMMS 연동 설계.
  `SLACK_WEBHOOK_URL` / `CMMS_MCP_URL`을 비워두면 둘 다 no-op.
- `docs/design/UI_UPGRADE_PLAN.md` — 프론트엔드 개선 이력, 완전히 종료됨
- `LICENSE` — MIT
