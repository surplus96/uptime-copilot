# Uptime Copilot

[![English](https://img.shields.io/badge/English-switch-555555?style=for-the-badge)](README.md)
[![한국어](https://img.shields.io/badge/한국어-현재-0c7c8c?style=for-the-badge)](README_KOR.md)

[![Backend checks](https://github.com/surplus96/uptime-copilot/actions/workflows/backend-checks.yml/badge.svg)](https://github.com/surplus96/uptime-copilot/actions/workflows/backend-checks.yml)

OpenAI 기반 RAG + 멀티에이전트 백엔드와 Streamlit 프론트엔드로 구성된 프로젝트입니다.
제조 설비 정비 도메인을 배경으로 하며, Azure Predictive Maintenance 데이터셋을 사용합니다.

> 📄 **[포트폴리오 케이스 스터디 →](docs/PORTFOLIO_KOR.md)** — 아키텍처 다이어그램, 핵심 로직, 주요 엔지니어링 결정.

## 폴더 구조

```
uptime-copilot/
├── archive/               Azure PdM 원본 CSV (직접 다운로드 필요 - 아래 "데이터 준비" 참고)
├── backend/                FastAPI 백엔드
│   ├── main.py               진입점 (uvicorn main:app)
│   ├── core/                  Harness(입출력 검증) + 시스템 프롬프트
│   ├── rag/                    RAG 파이프라인(하이브리드 검색 + Multi-Query) + docs/
│   │                             (`pump_manual.py`는 에이전트가 직접 읽는 정적 조회용 dict —
│   │                             RAG로 검색하지 않음. "아키텍처 원칙" 참고)
│   ├── agent/                  LangGraph 멀티에이전트 그래프(라우팅 + HITL + 이벤트 스캐너)
│   ├── data/                    PdM 데이터 적재/조회 계층 + 이벤트 스토어(SQLite)
│   │                             (`sim_*.py`: 자동 열화 시뮬레이터 —
│   │                             "아키텍처 원칙"과 `docs/design/SIMULATOR_PLAN.md` 참고)
│   ├── store/                    생성되는 상태(pdm_telemetry.db, checkpoints.db,
│   │                             chroma_db/) — gitignore 대상. 소스와 분리해서 Docker 볼륨을
│   │                             마운트해도 코드를 덮어쓰지 않음
│   ├── tests/                    pytest 회귀 테스트 — 아래 "검사 실행" 참고
│   ├── notify.py                 Slack 알림 — 선택 사항, "환경 변수" 참고
│   ├── cmms_client.py             CMMS 작업지시서 전송 — 선택 사항, docs/design/PHASE_7_PLAN.md 참고
│   ├── pyproject.toml             ruff / mypy / pytest 설정 — 아래 "검사 실행" 참고
│   └── Dockerfile
├── frontend/                Streamlit 채팅 UI
│   └── Dockerfile
├── .github/workflows/       CI: 모든 push/PR마다 ruff + mypy + pytest 실행
└── docker-compose.yml       아래 "Docker Compose로 실행" 참고
```

## 사전 요구사항

- Python ≥3.10 (3.12에서 개발/테스트. 코드 전반에서 `X | None` 유니온 문법 사용)
- Docker + Docker Compose (아래 수동 venv 설정을 건너뛰고 싶은 경우)

## Docker Compose로 실행 (권장)

데이터셋은 어떤 방식이든 직접 다운로드해야 합니다(재배포 불가라 이미지에 포함할 수 없음).
따라서 아래 "데이터 준비"는 실행 방식과 관계없이 필요합니다.

```bash
# 1) 데이터 준비(아래 참고) — 먼저 데이터셋을 archive/에 다운로드

# 2) 설정
cp backend/.env.example backend/.env
# backend/.env 채우기 (OPENAI_API_KEY 필수, 나머지는 선택 —
# 아래 "환경 변수" 참고)

# 3) 두 서비스 빌드 및 실행
docker compose up -d --build
```

- 프론트엔드: http://localhost:8501 — 백엔드: http://localhost:8000 (`/docs`에서 대화형 API 문서)
- **완전히 처음 켜는 경우(cold boot) 최대 10분 정도 걸릴 수 있습니다**(헬스체크가
  `start_period: 600s`까지 허용): 백엔드가 아래에 언급된 약 470MB 임베딩 모델을 컨테이너
  안에서 내려받습니다. `docker compose logs -f backend`로 진행 상황을 볼 수 있고, 완료
  전까지는 "starting"(아직 healthy 아님) 상태이며 프론트엔드 컨테이너는 자동으로
  기다립니다 — 별도 조치 없이 기다리면 됩니다. 이후엔 모델이 named volume(`hf_cache`)에
  캐시되어 재빌드해도 몇 분이 아니라 몇 초 만에 healthy가 됩니다.
- **최초 1회 데이터 적재는 컨테이너 안에서 직접 실행해야 합니다**(이미지에는 의도적으로 데이터를
  넣지 않음 — `backend/.dockerignore` 참고. 그래서 처음 `docker compose up`하면 빈 DB로 시작):
  ```bash
  docker compose exec backend python data/pdm_dataloader.py
  ```
- **고장 위험 예측 모델도 똑같이 최초 1회 실행이 필요합니다** — `backend/store/`가 이미지에
  안 들어가는 별도 볼륨이라, 새 컨테이너엔 학습된 모델도 없습니다. 이걸 안 하면
  `_diagnose_machine()`이 (훨씬 약한) Z-score 기준선으로 조용히 대체되고
  `위험도 모델 파일이 없어 Z-score만으로 판정합니다`라는 경고 로그가 뜹니다 — 이건
  이 단계를 실행하기 전까지는 정상적으로 나오는 로그이지 뭔가 고장난 게 아닙니다:
  ```bash
  docker compose exec backend python -m ml.build_features
  docker compose exec backend python -m ml.train
  ```
- 상태(`backend/store/`: SQLite DB와 RAG 인덱스)는 이미지가 아니라 Docker named volume에
  저장되어 `docker compose down` / `up` 사이에도 유지됩니다. `docker compose down -v`만
  이를 삭제합니다(그 경우 위 적재 단계를 다시 실행해야 하고, `hf_cache` 볼륨도 같이
  지워져서 임베딩 모델도 다시 받아야 함).
- 두 서비스 모두 `127.0.0.1`에만 바인딩됩니다(아래 수동 설정과 동일) — LAN에 노출되지 않습니다.
- 중지는 `docker compose down`.
- **`docker-compose.yml`은 백엔드 기본값을 `LLM_PROVIDER=ollama`로 강제합니다**(코드 자체의
  기본값 — 도커 없이 직접 실행할 때 등 — 은 `openai`입니다. 이 override는
  `docker-compose.yml`의 `environment:` 블록에 있고, `backend/.env`보다 항상 우선합니다.
  이 트레이드오프가 실제로 뭘 의미하는지는 아래 평가 기준선 참고). 컨테이너가 접근하려면
  호스트에서 `OLLAMA_HOST=0.0.0.0 ollama serve`로 Ollama를 켜야 합니다(기본값인 루프백
  전용 바인딩은 컨테이너에서 접근 불가) — `docker-compose.yml`이 `OLLAMA_BASE_URL`을 이미
  `host.docker.internal`로 맞춰뒀습니다. 모델은 한 번 `ollama pull qwen3:8b`로 받아두면
  됩니다. 컨테이너에서 클라우드 모델을 쓰고 싶으시면 `docker-compose.yml`의
  `LLM_PROVIDER: openai`로 바꾸시면 됩니다.

## Docker 없이 실행

```bash
# 백엔드
cd backend
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # Windows: copy .env.example .env
# .env 채우기 (OPENAI_API_KEY 필수, 나머지는 선택 - 아래 "환경 변수" 참고)
```

### 데이터 준비 (최초 1회, `pip install` 이후 첫 실행 전)

1. [Microsoft Azure Predictive Maintenance 데이터셋](https://www.kaggle.com/datasets/arnabbiswas1/microsoft-azure-predictive-maintenance)을
   다운로드합니다 (PdM_machines.csv, PdM_errors.csv, PdM_maint.csv, PdM_failures.csv,
   PdM_telemetry.csv — PdM_telemetry.csv만 약 87만 행이라 적재에 시간이 좀 걸립니다).
2. 파일들을 `uptime-copilot/archive/` 아래에 둡니다.
3. 최초 1회 SQLite 적재를 실행합니다 (`backend/`에서, 위 venv 활성화 상태로):
   ```bash
   python data/pdm_dataloader.py
   ```
4. 고장 위험 예측 모델의 피처를 만들고 학습합니다(이것도 최초 1회 — 안 하면
   `_diagnose_machine()`이 더 약한 Z-score 기준선으로 대체되고 경고 로그가 뜹니다):
   ```bash
   python -m ml.build_features
   python -m ml.train
   ```

### 첫 실행

```bash
uvicorn main:app --reload --port 8000
```

최초 실행 시 `intfloat/multilingual-e5-small` 임베딩 모델(약 470MB)을 Hugging Face에서 내려받고
RAG 인덱스를 만듭니다 — 몇 분과 네트워크 연결이 필요하며, 멈춘 것처럼 보이지만 정상입니다.
이후 재시작은 빠릅니다. 다운로드가 몇 분째 진행이 없으면 uvicorn 실행 전에 셸에서
`HF_HUB_DISABLE_XET=1`을 설정해보세요 — 일부 네트워크 환경에서는 최신 "xet" 전송 경로
자체가 막혀 있습니다. `docker-compose.yml`에는 컨테이너 경로용으로 이미 설정돼 있습니다.

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
ruff check .                   # 린트 + import 순서 (설정: pyproject.toml)
mypy                           # data/ + cmms_client.py 타입 검사, 항상 0 에러 유지
pytest tests/ --ignore=tests/test_rag_dedup.py   # 빠른 경로 - 임베딩 모델 테스트 제외
pytest tests/                                     # 전체 스위트, 임베딩 모델 다운로드/로드 포함
```

CI(`.github/workflows/backend-checks.yml`)가 모든 push와 PR마다 `ruff check`, `mypy`,
빠른 경로 pytest를 실행합니다.

## 평가 기준선

40문항 골든셋(`backend/tests/eval/golden.jsonl`)으로 실제 LLM 호출을 통해 에이전트의
라우팅과 설비번호 추출 정확도를 확인합니다. 실제 API 토큰 비용이 들어서 기본 테스트
실행과 CI에서는 제외되어 있고, 아래처럼 명시적으로 실행해야 합니다:

```bash
cd backend
pytest tests/eval/test_golden.py -m eval -v -s
```

| 지표 | 정확도 | 측정일 |
|---|---|---|
| 라우터 (카테고리) | 90.0% | 2026-09-23 |
| 설비번호 추출 | 100.0% | 2026-09-23 |
| 번호 없을 때 되묻기 | 100.0% | 2026-09-23 |

실행할 때마다 문항별 상세 결과가 `backend/tests/eval/results/<timestamp>.json`에 저장됩니다
(gitignore 대상 — 버전 관리 대신 재실행으로 재현). 기대 긴급도는 골든셋 파일에 일부러
고정하지 않았습니다 — 백그라운드 시뮬레이터가 매 틱마다 실제 설비 상태를 바꾸기 때문에,
긴급도 정답은 평가 실행 시점에 `_diagnose_machine()`으로 그때그때 실측합니다.

### 로컬(Ollama) vs 클라우드

같은 골든셋을 완전 로컬 `LLM_PROVIDER=ollama`(Qwen3-8B, Apple Silicon, GPU 가속 없이 Metal만)로
돌린 결과입니다:

| 지표 | 클라우드 (gpt-5.6-luna) | 로컬 (Ollama Qwen3-8B) |
|---|---|---|
| 라우터 정확도 | 90.0% | 77.5% |
| 설비번호 추출 | 100.0% | 100.0% |
| 번호 없을 때 되묻기 | 100.0% | 100.0% |
| 호출당 평균 지연 | 약 1.2초 | 13.6초(라우팅) / 10.8초(추출) |

추출·되묻기는 로컬에서도 똑같이 나옵니다 — 범위가 좁고 형태가 정해진 작업이라 그렇습니다.
라우팅은 12.5%p 떨어지고, 모든 호출이 자릿수 하나만큼 느려집니다. 골든셋에는 없지만(라우팅·추출만
다룸) 안전/생산/정비 관점 평가를 따로 확인해 보니, 클라우드에서는 안 나오던 모순된 출력이
로컬에서 나왔습니다 — `risk_level: "중간"`인데 `requires_shutdown: true`(정지 필요). CP-U2
교차 검토(`docs/decisions.md`)가 이미 우려했던 지점이 실측으로 재현된 것입니다. 결론: 어댑터
(`LLM_PROVIDER=ollama`)는 지금 그대로 동작하지만, 완전 로컬 전환은 API 비용 0원·완전 폐쇄망
운영을 얻는 대신 정확도와 지연에서 측정 가능한 손실을 감수해야 합니다 — 폐쇄망 환경이라면
감수할 가치가 있지만, 아무 대가 없는 무료 업그레이드는 아닙니다.

## 환경 변수 (`backend/.env`)

| 변수 | 필수 | 미설정 시 동작 |
|---|---|---|
| `LLM_PROVIDER` | 선택 | 코드 기본값은 `openai`. `ollama`로 설정하면 완전히 로컬 Ollama 서버로만 동작(`backend/core/llm_provider.py`) — API 키·인터넷 불필요, 대신 실측 가능한 정확도/지연 손실 있음(아래 평가 기준선 참고). **`docker-compose.yml`은 이 값을 `ollama`로 강제** — `.env`에 뭐라고 써도 도커 경로에선 이게 이깁니다. 위 "Docker Compose로 실행" 참고. |
| `OPENAI_API_KEY` | **`LLM_PROVIDER=ollama`가 아니면 필수** | 백엔드가 시작되지 않음. 도커 경로는 compose가 기본으로 `LLM_PROVIDER=ollama`를 설정하므로 필요 없음. |
| `OPENAI_MODEL` | 선택 | 기본값 `gpt-5.6-luna`. `LLM_PROVIDER=openai`일 때만 사용 |
| `OLLAMA_BASE_URL` | 선택 | 기본값 `http://localhost:11434/v1`. `LLM_PROVIDER=ollama`일 때만 사용 |
| `OLLAMA_MODEL` | 선택 | 기본값 `qwen3:8b`. `LLM_PROVIDER=ollama`일 때만 사용 — 먼저 `ollama pull qwen3:8b`로 받아야 함 |
| `LANGCHAIN_TRACING_V2` / `LANGCHAIN_API_KEY` / `LANGCHAIN_PROJECT` | 선택 | LangSmith 트레이싱 비활성화 |
| `SLACK_WEBHOOK_URL` | 선택 | 긴급/주의 감지 시 Slack 알림을 조용히 건너뜀 (`backend/notify.py`) |
| `CMMS_MCP_URL` + `CMMS_MCP_TOKEN` | 선택 | 승인 시 CMMS 작업지시서 전송을 조용히 건너뜀 (`backend/cmms_client.py`). 설정한다면 실행 중인 Atlas-MCP + Atlas CMMS 인스턴스가 필요 — `docs/design/PHASE_7_PLAN.md` 참고 |
| `ALLOWED_HOSTS` | 선택 | 항상 허용되는 `localhost`/`127.0.0.1` 외에 `TrustedHostMiddleware`(`backend/main.py`)를 통과시킬 추가 호스트명(쉼표 구분). `docker-compose.yml`이 `backend`로 자동 설정하며(이 키에 한해 compose의 `environment:` 블록이 `backend/.env` 값보다 항상 우선) — 백엔드를 다른 호스트명이나 리버스 프록시 뒤에 둘 때만 직접 설정 필요. |
| `BACKEND_URL` (프론트엔드용, `backend/.env` 아님) | 선택 | Streamlit 앱이 백엔드를 찾는 주소. 기본값 `http://localhost:8000`, `docker-compose.yml`이 `http://backend:8000`으로 자동 설정. |
| `SIM_TICK_SECONDS` | 선택 | 기본값 `60` — 자동 열화 시뮬레이터가 켜져 있을 때 실제 몇 초마다 한 번씩 전진할지 |
| `SIM_HOURS_PER_TICK` | 선택 | 기본값 `1` — 한 번 전진할 때 시뮬레이션 시간으로 몇 시간을 진행할지 |
| `SIM_SEED` | 선택 | 기본값 `42` — 시뮬레이터 난수 시드. `POST /simulator/reset`을 호출하면 이 시드로 새로 시작 |

> **`backend/.env.example`엔 데모용으로 조정된 시뮬레이터 값**(`SIM_TICK_SECONDS=15`,
> `SIM_HOURS_PER_TICK=12`)이 들어있습니다 — 위 표의 코드 기본값(`60`/`1`)이 아닙니다.
> 실제 코드 기본값 대비 약 48배 빠른 속도라, 데모에서 1분도 안 돼 이벤트가 뜹니다.
> 더 느린(실시간에 가까운) 속도를 원하면 이 두 줄을 지우거나 `60`/`1`로 바꾸세요.

> **호스트에 있는 Atlas-MCP를 Docker 안의 백엔드에서 사용할 때:** `backend` 컨테이너 안의
> `localhost`는 호스트 머신이 아니라 컨테이너 자신을 가리키므로
> `CMMS_MCP_URL=http://localhost:PORT/mcp`는 연결에 실패합니다.
> `http://host.docker.internal:PORT/mcp`를 쓰고, Atlas-MCP 쪽 `ALLOWED_HOSTS`에도
> `host.docker.internal:PORT`를 추가하세요(DNS 리바인딩 방어용 Host 헤더 검사가 그렇지 않으면
> 421로 거부합니다). `backend/cmms_client.py`의 루프백 검사는 이미 이 목적으로
> `host.docker.internal`을 허용 목록에 넣어두었습니다.

## 주요 API 엔드포인트

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/health` | 헬스 체크 |
| POST | `/rag/query` | RAG 기반 문서 Q&A |
| POST | `/agent/query` | 멀티에이전트 질의 — 긴급 건이면 `pending_approval` 반환 |
| POST | `/agent/resume` | HITL 승인/반려 결정 후 그래프 재개 |
| POST | `/scan` | 100대 설비 전체 스캔(LLM 미사용), 긴급/주의 건을 이벤트 스토어에 저장 |
| GET | `/events` | 대기 중인 감지 이벤트 목록 |
| POST | `/events/complete` | 이벤트를 완료 처리 — 보관되며, 진짜 새로운 근거가 있을 때만 다시 표면화 |
| POST | `/events/delete` | 기록 없이 이벤트 폐기 (다음 스캔에서 다시 나타날 수 있음) |
| POST | `/simulator/start` | 자동 열화 시뮬레이터 백그라운드 루프 시작 (현재 상태에서 이어감) |
| POST | `/simulator/stop` | 루프 정지 |
| GET | `/simulator/status` | 실행 여부, 시뮬레이션 시각, 열화 진행 중인 설비 목록, `has_stale_events`, 백그라운드 틱이 실패 중이면 `last_error`/`consecutive_failures`도 포함 |
| POST | `/simulator/inject` | 특정 설비를 강제로 강하게 열화시킴, 데모용 (`{"machine_id": 12}`, 선택적으로 `"signal"`: `volt`/`rotate`/`pressure`/`vibration` 중 하나, 생략하면 무작위) |
| POST | `/simulator/reset` | 시뮬레이터 상태/데이터 **및** 감지/완료 이벤트 테이블 전체 초기화 — 새로 시작하기 전에 호출. 자동으로는 지워지지 않음. `docs/design/SIMULATOR_PLAN.md` 참고 |

`/agent/query` 요청 예시:
```json
{"message": "Machine #12 has an error, what's wrong?", "thread_id": "unique-thread-id"}
```
응답이 `{"status": "pending_approval", "message": "..."}`이면, 같은 `thread_id`로
`{"thread_id": "...", "approved": true|false}`를 `/agent/resume`에 보내 실행을 이어갑니다.

## 아키텍처 원칙

- **Harness**: 각 모델 호출 전후로 입출력을 검증하는 결정론적 계층(`core/harness.py`) —
  정규식 기반 PII 체크(계산적)와 LLM-as-judge 체크(추론적)를 함께 사용.
- **RAG**: Multi-Query 재작성 + 하이브리드(BM25 + Dense) 검색, 자동 Faithfulness 채점.
- **Agent**: LangGraph `StateGraph` 기반. 질문을 진단 / 정비 일정 / 일반 문의 분기로 라우팅.
  진단은 3단계 긴급도 모델을 사용: 일반(normal) / 주의(caution — Z-score 텔레메트리 이상,
  확정 고장 아님) / 긴급(urgent — 실제 로그된 고장 기록). 긴급 건만 3개 관점 병렬 평가
  (안전 / 생산 / 정비)를 거쳐 Human-in-the-Loop(HITL) 승인을 기다리고, 주의 건은 바로
  작업지시서로 넘어갑니다. HITL 승인 상태는 메모리에만 두지 않고 SQLite
  (`backend/store/checkpoints.db`)에 체크포인트되어 `--reload`/재시작에도 유지됩니다.
  별도의 `/scan`은 동일한 진단 로직을 LLM 없이 100대 전체에 돌려 긴급/주의 건을 이벤트
  스토어에 저장하고, 완료 처리된 이벤트는 완료 시점 이후의 진짜 새로운 근거가 나타나야만
  이후 스캔에서 다시 표면화됩니다 — `backend/data/event_store.py` 참고.
- **자동 시뮬레이터**: `backend/data/sim_engine.py`/`sim_store.py`/`sim_query.py`/`sim_loop.py`가
  백그라운드 열화 모델을 돌립니다(설비별 상태 머신: HEALTHY → DEGRADING → FAULT → 고장+정비).
  실제 데이터셋에서 측정한 통계(편차 크기, 리드 타임, 오류 동시발생 등 — `docs/design/SIMULATOR_PLAN.md`
  참고)로 보정했습니다. 원본과 분리된 `sim_*` 테이블에만 기록하고(원본 읽기 전용 데이터는
  건드리지 않음), `asyncio` 백그라운드 태스크가 `SIM_TICK_SECONDS`마다 한 번씩 전진시키며,
  매 틱마다 증분 스캔과 새로 감지된 건을 묶은 Slack 알림이 뒤따릅니다. 완전히 선택 사항이라
  꺼져 있어도(기본값) 앱은 동일하게 동작합니다.
- **Data**: 모든 경로 상수는 현재 작업 디렉터리가 아니라 파일 자신의 위치(`Path(__file__).parent`)
  기준으로 해석하므로, 코드를 어디서 실행하거나 옮겨도 올바르게 동작합니다. 생성/변경되는 상태
  (`pdm_telemetry.db`, `checkpoints.db`, RAG Chroma 인덱스)는 `backend/store/` 아래에 두어,
  Docker named volume을 마운트해도 애플리케이션 코드를 가리지 않도록 소스와 분리했습니다.
- **Observability**: LangSmith 연동. 동일한 PII 정규식으로 만든 익명화기가 트레이스 전송 전에
  민감 정보를 마스킹합니다.

## 관련 문서

- `docs/PORTFOLIO_KOR.md` — 포트폴리오 독자를 위한 아키텍처/설계 케이스 스터디
  (다이어그램, 주요 엔지니어링 결정, 디버깅 사례)
- `docs/design/SIMULATOR_PLAN.md` — 자동 열화 시뮬레이터 설계 기록: 실측 보정 데이터, 상태 머신,
  백그라운드 루프, `/simulator/*` API
- `docs/design/PHASE_7_PLAN.md` — **선택적 연동, 이 프로젝트 실행에 필수 아님.** Slack 알림 + CMMS
  작업지시서 전송의 설계/진행 기록. `SLACK_WEBHOOK_URL` / `CMMS_MCP_URL` / `CMMS_MCP_TOKEN`을
  비워두면 두 기능 모두 동작하지 않고, 앱은 이 파일에 적힌 어떤 것도 없이 완전히 동작합니다.
- `docs/design/UI_UPGRADE_PLAN.md` — 프론트엔드 개선 작업 기록. 완전히 종료되었으며 진행 중인
  백로그가 아닌 이력으로 보관
- `LICENSE` — MIT
