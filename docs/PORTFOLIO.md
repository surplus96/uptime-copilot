# Uptime Copilot

> 제조 설비 예지보전(Predictive Maintenance)을 위한 RAG + 멀티에이전트 코파일럿.
> 실제 센서/오류/정비 이력 데이터를 근거로 설비 이상을 진단하고, 긴급 사안은
> Human-in-the-Loop 승인을 거쳐 Slack 알림과 CMMS 작업지시서 생성까지 자동으로
> 이어지는 end-to-end 파이프라인을 직접 설계·구현했습니다.

`FastAPI` · `LangGraph` · `Streamlit` · `RAG (Chroma)` · `SQLite` · `Model Context Protocol` · `Docker Compose` · `pytest`

| | | | |
|---|---|---|---|
| **100** 모니터링 설비 수 | **3-tier** 긴급도 판정 모델 | **16** 회귀 테스트 | **9** 전담 코드 리뷰 에이전트 |

---

## 01 · Overview — 무엇을, 왜 만들었나

Azure Predictive Maintenance 데이터셋(설비 100대, 텔레메트리 약 87만 행, 오류/정비/고장
이력 포함)을 기반으로, "설비에 이상이 생기면 근거를 갖고 진단하고, 조치가 필요하면
승인을 거쳐 실제 현장 도구(Slack, CMMS)까지 연결되는" 코파일럿을 목표로 했습니다.

> **핵심 설계 원칙 — "사실은 코드가 말하고, LLM은 해석만 한다."**
> 진단 근거(오류 로그, Z-score 이상치, 실제 교체 이력)와 표준 조치 절차는 전부 결정론적으로
> DB/코드에서 조회하고, LLM은 라우팅·요약·관점 평가처럼 판단이 필요한 부분에만 관여합니다.
> 작업지시서 문구를 LLM이 다시 서술하지 않기 때문에 구조적으로 사실 왜곡(hallucination)이
> 끼어들 자리가 없습니다.

## 02 · Architecture — 시스템 아키텍처

Streamlit 프론트엔드가 FastAPI 백엔드를 호출하고, 백엔드는 모든 모델 입출력을
Harness(결정론적 검증 계층)를 통과시킨 뒤 LangGraph 슈퍼바이저와 RAG 서비스로 위임합니다.
상태(텔레메트리·이벤트·HITL 체크포인트)는 SQLite에, 문서 임베딩은 Chroma에 저장되며,
승인된 긴급 건만 Slack과 Atlas CMMS(MCP)로 결정론적으로 전파됩니다.

```mermaid
flowchart LR
    UI["Streamlit UI"]
    subgraph BE["FastAPI Backend"]
        HN["Harness<br/>input / output 검증"]
        SV["LangGraph Supervisor"]
        RS["RAG Service<br/>hybrid + multi-query"]
        SC["Event Scanner<br/>/scan"]
    end
    subgraph DL["Data Layer"]
        DB[("SQLite<br/>telemetry / events / checkpoints")]
        VS[("Chroma<br/>vector index")]
    end
    subgraph EX["External Systems"]
        SL["Slack Webhook"]
        MC["Atlas-MCP<br/>(hardened fork)"]
        CM[("Atlas CMMS")]
    end

    UI <--> HN
    HN <--> SV
    HN <--> RS
    SV --> DB
    SC --> DB
    RS --> VS
    SV -- "승인됨" --> SL
    SV -- "승인됨" --> MC
    MC --> CM
```
*그림 1 — 서비스 경계와 데이터 흐름*

## 03 · Core Logic

### 3단계 긴급도 판정

진단은 항상 아래 세 등급 중 하나로 귀결되고, 등급에 따라 그래프의 경로 자체가 달라집니다.

| 등급 | 판정 근거 | 후속 동작 |
|---|---|---|
| 🟢 **일반** (normal) | 이상 징후 없음 | 바로 답변, 별도 조치 없음 |
| 🟡 **주의** (caution) | 텔레메트리 Z-score 이상치 — 아직 확정 고장은 아님 | 작업지시서 생성 후 바로 확정 (승인 불필요) |
| 🔴 **긴급** (urgent) | `PdM_failures.csv`에 실제 로그된 고장 기록 존재 | 안전/생산/정비 3-관점 병렬 평가 → **HITL 승인 대기** → 승인 시 Slack + CMMS 전파 |

### LangGraph 멀티에이전트 그래프

`StateGraph` 하나로 라우팅부터 승인까지 전 과정을 관리합니다. 긴급 건만
3개 관점 노드를 병렬 실행(`operator.add`로 결과를 머지)하고 `interrupt()`로 멈춰서
사람의 결정을 기다리며, 이 승인 상태는 SqliteSaver로 체크포인트되어 서버 재시작에도
살아남습니다.

```mermaid
flowchart TD
    START(("START")) --> route["route"]
    route -- "오류_진단" --> diagnosis["diagnosis"]
    route -- "정비_일정" --> schedule["schedule"] --> ENDs(("END"))
    route -- "일반_문의" --> general["general"] --> ENDg(("END"))
    diagnosis -- "일반" --> general2["general"] --> ENDg2(("END"))
    diagnosis -- "주의 / 긴급" --> manual["manual_lookup"]
    manual -- "주의" --> wo["work_order"]
    manual -- "긴급" --> safety["safety"]
    manual -- "긴급" --> production["production"]
    manual -- "긴급" --> maintenance["maintenance"]
    safety --> merge["merge_perspectives"]
    production --> merge
    maintenance --> merge
    merge --> wo
    wo -- "긴급" --> approval[["approval\n(HITL interrupt)"]]
    wo -- "주의" --> finalize["finalize"]
    approval --> finalize
    finalize --> ENDf(("END"))
```
*그림 2 — 노드/엣지는 `backend/agent/agent_service.py`의 실제 그래프 정의와 1:1로 대응*

### RAG · Harness

| 구성요소 | 내용 |
|---|---|
| RAG 검색 | BM25(sparse) + Dense 임베딩 하이브리드 검색, Multi-Query 재작성으로 질의를 여러 각도로 확장. 임베딩 모델은 `intfloat/multilingual-e5-small`, 벡터 저장소는 Chroma. |
| Faithfulness 채점 | 답변 생성 후 컨텍스트 대비 사실 충실도를 LLM-judge로 자동 채점 (RAG 특유의 "그럴듯하지만 근거 없는 답변" 문제 대응). |
| Harness | 모든 모델 입출력에 적용되는 결정론적 검증 계층. 정규식 기반 PII 체크(계산적) + LLM-judge 품질/충실도 체크(추론적)를 함께 사용. |

### 이벤트 스캐너 — 근거 기반 억제 로직

`/scan`은 LLM 없이 100대 설비를 전부 훑어 긴급/주의 건을 이벤트 스토어에 저장합니다.
완료 처리된 이벤트가 재감지될지 여부는 **벽시계 시간이 아니라 새로운 "근거"가
나왔는지**로 판단합니다 — 완료 시점 이후 새 오류 로그가 실제로 쌓여야만 다시
표면화되고, 그렇지 않으면 몇 번을 스캔해도 조용히 억제된 채로 남습니다.

## 04 · External Integrations — Slack 알림 · CMMS 작업지시서 (MCP)

승인 이후의 알림/작업지시서 생성은 에이전트가 "그때그때 판단해서" 실행하는 도구 호출이
아니라, 이미 결정이 끝난 뒤의 고정된 동작입니다. 그래서 LLM에게 도구 선택을 맡기는
`langchain-mcp-adapters` 대신, MCP Python SDK로 정해진 도구 하나(`create-work-order`)를
코드가 직접, 결정론적으로 호출하도록 설계했습니다.

> **Atlas CMMS**를 Docker Compose로 직접 셀프호스팅하고, 커뮤니티 MCP 서버(Atlas-MCP)를
> 포크해서 하드닝했습니다 — Bearer 토큰 상시 검사, 타이밍 공격 방지용 상수시간 비교
> (`timingSafeEqual`), 그리고 DNS 리바인딩을 막는 Host-헤더 화이트리스트
> (`ALLOWED_HOSTS`) 검사를 추가했습니다. 이 방어 로직은 이후 uptime-copilot 백엔드
> 자체의 `TrustedHostMiddleware` 확장에도 동일한 패턴으로 재사용했습니다.

## 05 · Engineering Notes — 실제로 부딪힌 문제들

겉보기엔 잘 도는 시스템도, 파고들면 조용히 틀린 값을 내고 있는 경우가 있습니다.
그중 재현·근본원인·수정까지 직접 끝까지 판 사례 네 가지입니다.

<details>
<summary><strong>BUG · 이벤트 억제 로직 — "스캔해도 아무것도 안 뜸", 시간 기준이 섞여 있었다</strong></summary>

완료 시각(`completed_at`, 실제 벽시계 시간)과 증거 시각(`evidence_at`, 데이터셋 자체
시간축)을 그대로 비교하고 있어서, 긴급 이벤트는 영구히 억제되고 주의 이벤트는 새 증거
없이도 재표면화되는 버그였습니다. `evidence_at`을 파이프라인 전체에 걸쳐 보존하고
증거-대-증거로만 비교하도록 고쳐서 해결했습니다.
</details>

<details>
<summary><strong>BUG · MCP 실패 처리 — CMMS가 거부해도 "성공"으로 착각한 이유</strong></summary>

MCP는 도구 실행 실패를 예외가 아니라 **성공 응답 안의 `isError` 필드**로 보고합니다.
`_create_work_order()`가 `call_tool()`의 반환값을 그냥 버려서, Atlas가 500으로 거부해도
코드는 정상 종료했습니다. 두 개의 독립적인 리뷰 에이전트(코드 품질 / 파이프라인)가
각자 이 문제를 지적했고, 실제로 존재하지 않는 assetId로 호출해 재현한 뒤 고쳤습니다.
</details>

<details>
<summary><strong>INFRA · 컨테이너 네트워킹 — 같은 버그를 막는 방어벽 두 개가 서로를 막았다</strong></summary>

Docker화된 백엔드가 호스트에서 떠 있는 CMMS에 붙으려면 `host.docker.internal`이
필요했는데, 이 주소가 uptime-copilot 자체의 DNS 리바인딩 방어와 Atlas-MCP의
Host-헤더 화이트리스트, **양쪽 모두**에 막혔습니다. 우회가 아니라 두 방어 로직
모두에 정확한 예외 호스트를 등록하는 방식으로 풀었습니다.
</details>

<details>
<summary><strong>UX · 실측 기반 의사결정 — "줄바꿈이 안 살아있다", 추측 대신 헤드리스 브라우저로 확인</strong></summary>

Slack/CMMS 알림 가독성 이슈를 고치면서, CMMS 화면이 개행을 렌더링하지 않는다는 가설을
세웠습니다. 이 가설을 근거로 U+2028(LINE SEPARATOR) 트릭까지 실제로 헤드리스 Chrome에
렌더링해 스크린샷으로 비교 검증했고, 텍스트만으로는 해결 불가능함을 확인한 뒤에야
(다른 오픈소스 프로젝트의 프론트엔드를 건드리지 않는 선에서) 구분자 기반의 대안으로
스코프를 확정했습니다.
</details>

## 06 · Quality & Process — 테스트 · 리뷰 체계

| 항목 | 내용 |
|---|---|
| 회귀 테스트 | 발견한 버그마다 pytest 테스트를 추가하고, **수정 전 코드로 되돌려서 테스트가 실제로 빨간불이 되는지**까지 직접 검증한 뒤 원복 — 형식적인 테스트가 아니라는 걸 증명하는 절차를 고정 루틴으로 사용. |
| 전담 리뷰 에이전트 | 보안 / 코드 품질 / 인터페이스 / 파이프라인·모델 운용 / 문서 / 디버깅 / 빌드-패키징 / 성능 / 테스트 유효성, 9개 영역으로 나눠 각자의 관점에서만 리뷰하는 서브에이전트 구성 — 같은 맥락에서 작성한 코드를 같은 맥락에서 리뷰하면 결함이 그대로 통과한다는 전제에서 설계. |
| Observability | LangSmith 트레이싱 연동, PII 정규식을 재사용한 익명화 처리 후 트레이스 전송. |

## 07 · Stack — 기술 스택

`FastAPI` `LangGraph` `LangChain` `OpenAI API` `Streamlit` `SQLite` `Chroma`
`HuggingFace sentence-transformers` `rank_bm25` `Model Context Protocol SDK`
`LangSmith` `Docker / Docker Compose` `pytest / pytest-asyncio`

---

<sub>Uptime Copilot — Predictive Maintenance Copilot · Case study</sub>
