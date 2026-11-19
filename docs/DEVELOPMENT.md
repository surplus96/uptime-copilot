# 개발 가이드

## 요청과 상태의 흐름

| 구성 요소 | 책임 |
| --- | --- |
| `frontend/web` | 설비·이벤트 조회, 진단 요청, 승인 대기와 전송 결과 표시. 브라우저는 같은 출처의 `/api/`를 사용한다. |
| `backend/main.py`, `ui_routes.py`, `simulator_routes.py` | FastAPI 요청 검증, 서비스 초기화, 조회 및 시뮬레이션 제어 |
| `backend/data` | 원본 CSV 적재, 운영 시각 변환, 센서 조회, 열화 시뮬레이션, 이벤트·재고·감사·전송 이력 저장 |
| `backend/ml` | 원본 날짜 기준 피처 생성, 부품별 모델 학습과 위험 점수 추론 |
| `backend/rag` | 로컬 매뉴얼을 청크로 분할하고 BM25와 벡터 검색으로 근거를 조회 |
| `backend/agent` | LangGraph 진단 흐름, 작업지시서 생성, 승인 중단점과 결정 재개 |

첫 기동 시 운영 DB는 원본의 마지막 시각을 서울 시간 기준 기동 시점의 정각에 맞추고 해당 연도의 기록을 사용한다. 기준 시각은 저장하므로 재기동해도 같은 데이터의 날짜가 다시 이동하지 않는다. 원본 이력은 조회에 쓰이고 신규 사건 탐지는 저장된 기준 시각 이후부터 시작한다. 학습 파이프라인은 원본 CSV를 직접 읽는다.

시뮬레이터는 전체 설비 또는 선택 설비의 센서·오류·정비 기록을 생성한다. 열화 주입과 실행 시작은 별도 요청이다. 단일 실행에서도 공통 데이터 시각이 전진하므로 다른 설비의 마지막 관측은 오래된 것으로 표시될 수 있다.

## 진단 API의 호출 순서

| 요청 | 입력과 동작 |
| --- | --- |
| `POST /scan` | 전체 설비를 스캔해 긴급·주의 이벤트를 저장하고 이번 스캔의 감지 건수를 반환한다. |
| `POST /agent/query` | 새 `thread_id`와 `message`로 진단을 요청한다. 작업지시서가 승인 대기 상태가 되면 같은 스레드를 사용해 결정을 전달한다. |
| `GET /agent/status/{thread_id}` | 저장된 상태를 다시 읽는다. 요청 결과를 받기 전에 연결이 끊어졌을 때도 이 경로에서 먼저 확인한다. |
| `POST /agent/resume` | `thread_id`와 불리언 `approved`로 승인 또는 반려한다. |
| `POST /agent/cmms/retry` | 승인된 긴급 작업지시서의 외부 ID가 확인된 경우 같은 ID에 점검 항목 등록을 재시도한다. |
| `POST /rag/query` | `question`으로 매뉴얼을 검색하고 응답의 `verified`와 `verified_reason`으로 근거 검증 결과를 확인한다. |

조회 예시는 `curl http://localhost:8000/machines/84/status`로 실행할 수 있다. 전체 요청·응답 스키마와 입력 제약은 실행 중인 백엔드의 `/docs`에서 확인한다.

## 설정과 영속 데이터

- Compose의 `backend_store`에는 운영 SQLite, LangGraph 체크포인트, Chroma 인덱스와 학습 산출물이 저장된다. `hf_cache`는 임베딩 모델 캐시다. 원본 CSV는 컨테이너의 `/archive`에 읽기 전용으로 연결된다.
- 호스트의 Ollama를 사용할 때는 Compose의 `LLM_PROVIDER`를 `ollama`로 바꾸고 컨테이너에서 `OLLAMA_BASE_URL`에 접근할 수 있도록 구성한다. 모델 이름은 `OLLAMA_MODEL`로 설정한다. 기본값은 `qwen3:8b`다.
- `OFFLINE=1`은 로컬 LLM과 사전 준비한 임베딩 캐시가 필요하다. 기동 시 목적지와 프록시 설정을 검증하고 원격 추적과 Slack 전송을 끈다. Compose의 provider 설정이 env 파일보다 우선한다.
- `SIM_TICK_SECONDS`는 실제 경과 초, `SIM_HOURS_PER_TICK`은 한 틱에 진행할 시뮬레이션 시간이다. 예제 설정은 15초마다 12시간을 진행한다.
- `SLACK_WEBHOOK_URL`은 선택적 알림 설정이다. `GET /audit_log`에서 에이전트 단계와 승인·전송 결과를 조회한다. 외부 작업지시서 설정은 [CMMS 전송 가이드](CMMS_DELIVERY.md)에 정리했다.

## 개발 환경 검사

백엔드 CI는 Python 3.12를 사용한다. 아래 명령은 저장소 루트에서 시작하며 가상환경을 활성화한 상태를 가정한다.

```sh
cd backend
python -m pip install -r requirements.txt
python -m pip install ruff==0.16.8 mypy==2.3.1
python -m ruff check .
python -m mypy
python -m pytest tests/ --ignore=tests/test_rag_dedup.py
```

임베딩 초기화까지 검사하려면 마지막 명령에서 `--ignore` 옵션을 제거한다. 실제 LLM 평가에는 별도 자격 증명과 `-m eval` 선택이 필요하다. React 테스트와 빌드 명령은 [프런트엔드 개발 가이드](../frontend/web/README.md)에 있다.
