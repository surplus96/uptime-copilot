"""
FastAPI 백엔드

실행 방법:
    uvicorn main:app --reload --port 8000
"""
# ruff: noqa: E402 - dotenv/offline_guard.enforce_offline_env()가 HF_HUB_OFFLINE/
# LANGCHAIN_TRACING_V2를 다른 모든 import보다 먼저 세팅해야 한다(huggingface_hub는
# 이 값을 import 시점에 한 번 읽어 캐싱하므로, sentence-transformers/langchain을
# 끌어오는 rag_service를 import한 뒤에 세팅하면 이미 늦다 - 2026-09-29 CP-M2 교차
# 검토로 발견).
from dotenv import load_dotenv

load_dotenv()

from core import offline_guard  # noqa: E402

offline_guard.enforce_offline_env()

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from langgraph.checkpoint.sqlite import SqliteSaver
from langsmith.anonymizer import create_anonymizer
from pydantic import BaseModel, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from agent import agent_service
from core import llm_provider
from core.harness import (
    PII_PATTERNS,
    HarnessRejectedError,
    check_output_forbidden_words,
    judge_faithfulness,
    validate_input,
)
from data import audit_log, event_store, sim_loop, sim_store
from rag import rag_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

DEFAULT_MODEL = llm_provider.get_model()

offline_guard.refuse_unsafe_startup_combo()

# 2026-09-29 code-quality-reviewer 지적(M4): 여기만 llm_provider._provider()의
# 정규화를 안 거치고 원시 값을 직접 비교하고 있었다 - LLM_PROVIDER=OpenAI(대문자)면
# 이 조건이 False가 되어 키 없이도 기동 검사를 통과하고, 첫 실제 요청에서야 401로
# 실패했다. get_provider_name()으로 통일한다(이미 위에서 llm_provider.get_model()을
# 호출했으므로, 값이 알 수 없는 것이었다면 그 시점에 이미 실패했을 것이다).
if llm_provider.get_provider_name() == "openai" and not os.getenv("OPENAI_API_KEY"):
    raise RuntimeError("OPENAI_API_KEY가 .env에 설정되어 있지 않습니다 (LLM_PROVIDER=openai일 때 필수).")

# LangSmith로 나가는 트레이스에서 PII를 마스킹 (harness의 PII_PATTERNS 재사용 - 같은 기준으로 응답/트레이스 양쪽 방어)
anonymizer = create_anonymizer([
    {"pattern": pattern.pattern, "replace": f"<{label}>"}
    for label, pattern in PII_PATTERNS.items()
])
langsmith_client = offline_guard.get_langsmith_client(anonymizer)

client = offline_guard.wrap_openai(
    llm_provider.get_client(),
    tracing_extra={"client": langsmith_client},
)



# HITL 승인 대기 상태(그래프 체크포인트)를 디스크에 남긴다 - InMemorySaver는 uvicorn --reload나
# 프로세스 재시작마다 대기 중인 승인을 전부 날려버려서 SqliteSaver로 교체함.
CHECKPOINT_DB_PATH = Path(__file__).parent / "store" / "checkpoints.db"
CHECKPOINT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)  # 최초 실행(예: 도커 첫 부팅) 시 store/가 아직 없을 수 있음


async def _warm_up_ollama() -> None:
    """Ollama는 유휴 시간이 지나면 모델을 메모리에서 내렸다가 다음 요청에서 디스크부터
    다시 로드한다 - 첫 실제 사용자 요청이 이 콜드로드 지연까지 떠안지 않도록, 기동 직후
    백그라운드로 더미 호출 한 번을 보내 모델을 미리 올려둔다. 헬스체크/기동을 막지 않게
    fire-and-forget으로 실행하고 실패해도 무시한다(2026-09-25, 지연시간 개선)."""
    try:
        agent_service.client.chat.completions.create(
            model=agent_service.MODEL,
            max_completion_tokens=1,
            messages=[{"role": "user", "content": "ping"}],
        )
        logging.getLogger(__name__).info("Ollama 워밍업 완료")
        audit_log.log_event("llm_call", target="warm_up", summary="Ollama 워밍업 핑",
                             provider=llm_provider.get_provider_name())
    except Exception as e:
        logging.getLogger(__name__).warning(f"Ollama 워밍업 실패(무시하고 계속 진행): {e}")
        audit_log.log_event("llm_call", target="warm_up", summary="Ollama 워밍업 핑",
                             result="실패", provider=llm_provider.get_provider_name())


@asynccontextmanager
async def lifespan(app: FastAPI):
    rag_service.initialize_rag(llm_provider.get_api_key(), DEFAULT_MODEL, langsmith_client, base_url=llm_provider.get_base_url())
    event_store.init_event_table()
    sim_store.init_sim_tables()
    # 2026-09-28 교차 검토 지적: 이게 없으면 새 볼륨(CI, 첫 배포)에서 parts_master
    # 테이블이 아예 없어 긴급/주의 대화가 500 에러, 백그라운드 스캔은 매 tick마다
    # 조용히 실패한다. 합성 데이터라 크기가 작고 외부 파일도 안 필요해서(pdm_dataloader
    # 처럼 수동 1회 실행이 아니라) 기동 시 자동 생성해도 비용이 거의 없다.

    from data.parts_master import generate_parts_master, init_parts_table
    audit_log.init_audit_table()
    init_parts_table()
    generate_parts_master()
    sim_task = asyncio.create_task(sim_loop.run_forever())
    try:
        with SqliteSaver.from_conn_string(str(CHECKPOINT_DB_PATH)) as checkpointer:
            agent_service.initialize_agent(langsmith_client, checkpointer)
            if llm_provider.get_provider_name() == "ollama":
                asyncio.create_task(_warm_up_ollama())
            yield
    finally:
        # finally: yield에서 예외가 올라와도 태스크를 반드시 정리한다.
        sim_task.cancel()
        # wait_for로 상한을 건다 - 틱이 도는 중이면 취소가 즉시 먹지 않는데,
        # docker stop의 유예(stop_grace_period)가 끝나면 SIGKILL 당한다.
        try:
            await asyncio.wait_for(sim_task, timeout=5)
        except (asyncio.CancelledError, TimeoutError):
            pass




app = FastAPI(title=f"AI Agent RAG Backend ({llm_provider.get_provider_name()})", lifespan=lifespan)

# ---------- 커스텀 예외 ---------------


class LLMAPIError(Exception):
    """LLM API 호출 자체가 실패했을 때 발생시키는 도메인 예외."""
    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


allowed_hosts = ["localhost", "127.0.0.1"] + [
    h.strip() for h in os.getenv("ALLOWED_HOSTS", "").split(",") if h.strip()
]
app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

# Streamlit(로컬 개발 시 기본 8501 포트)에서의 요청을 허용
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8501",
        "http://127.0.0.1:8501",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(LLMAPIError)
async def llm_api_error_handler(request: Request, exc: LLMAPIError):
    logger.error(f"LLM API 호출 실패: {exc.detail}")
    return JSONResponse(
        status_code=502,
        content={"error": "llm_api_error", "detail": exc.detail},
    )


@app.exception_handler(HarnessRejectedError)
async def harness_rejected_handler(request: Request, exc: HarnessRejectedError):
    logger.warning(f"하네스 검증 실패: {exc.reason}")
    return JSONResponse(
        status_code=400,
        content={"error": "harness_rejected", "reason": exc.reason},
    )


# ---------- 요청/응답 스키마 ----------

class RAGRequest(BaseModel):
    question: str

    @field_validator("question")
    @classmethod
    def question_must_not_be_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("question은 빈 문자열이거나 공백만으로 구성될 수 없습니다.")
        return v

class RAGResponse(BaseModel):
    question: str
    answer: str
    context: str | None = None
    verified: bool = True
    verified_reason: str | None = None  # verified=False일 때만 채움 - 실패 사유를 프론트가 추측하지 않게


class AgentQueryRequest(BaseModel):
    message: str
    thread_id: str

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("message는 빈 문자열이거나 공백만으로 구성될 수 없습니다.")
        return v


class AgentResumeRequest(BaseModel):
    thread_id: str
    approved: bool


# ---------- 라우트 ----------

@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "llm_provider": llm_provider.get_provider_name(),
        "llm_model": llm_provider.get_model(),
        "offline": offline_guard.is_offline(),
    }


@app.post("/rag/query", response_model=RAGResponse)
@offline_guard.traceable(name="rag_query", client=langsmith_client)
def rag_query(req: RAGRequest):
    """매뉴얼 Q&A 보조 기능 - docs/ 폴더에 적재된 문서만 근거로 답한다. 일반 잡담이나
    설비 진단은 이 엔드포인트의 역할이 아니다(설비 진단은 /agent/query가 담당)."""

    validate_input(req.question)

    # 2026-09-29 CP-M2 교차 검토 지적: 감사 로그 호출이 성공 경로 맨 끝에만 있어서,
    # RAG 자체 실패나 faithfulness 거부처럼 오히려 더 알아야 할 케이스가 하나도
    # 기록되지 않았다 - finalize_node처럼 결과 분기마다 남기도록 고친다.
    try:
        context, answer = rag_service.answer_with_context(req.question)
    except Exception as e:
        audit_log.log_event("llm_call", target="rag_query", summary=req.question[:50],
                             result="실패", provider=llm_provider.get_provider_name())
        raise LLMAPIError(str(e))

    check_output_forbidden_words(answer)

    faithfulness_result = judge_faithfulness(client, context, answer)
    if not faithfulness_result.get("pass", True):
        audit_log.log_event("llm_call", target="rag_query", summary=req.question[:50],
                             result="거부됨", provider=llm_provider.get_provider_name())
        raise HarnessRejectedError(
            f"RAG 답변이 검색 문맥에 근거하지 않음(hallucination 의심): {faithfulness_result.get('reason')}"
        )
    audit_log.log_event("llm_call", target="rag_query", summary=req.question[:50],
                         result="성공", provider=llm_provider.get_provider_name())
    verified = faithfulness_result.get("score") is not None
    verified_reason = None if verified else faithfulness_result.get("reason")
    
    return RAGResponse(
        question=req.question, answer=answer, context=context,
        verified=verified, verified_reason=verified_reason,
    )


@app.post("/agent/query")
def agent_query(req: AgentQueryRequest):
    """멀티에이전트 그래프에 질문을 보낸다. 긴급 사안이면 승인 대기 상태로 응답할 수 있다."""
    validate_input(req.message)
    return agent_service.start_agent(req.message, req.thread_id)


@app.post("/agent/resume")
def agent_resume(req: AgentResumeRequest):
    """승인 대기 중인 요청에 사람의 결정을 전달해서 재개한다."""
    return agent_service.resume_agent(req.thread_id, req.approved)

@app.get("/agent/status/{thread_id}")
def agent_status(thread_id: str):
    pending = agent_service.check_pending(thread_id)
    return pending or {"status": "not_found"}


@app.post("/scan")
def trigger_scan():
    """전체 설비를 스캔해서 긴급/주의로 판정된 설비를 이벤트 저장소에 적재한다."""
    detected = agent_service.scan_all_machines()
    return {"detected_count": len(detected), "events": detected}

@app.post("/simulator/start")
def simulator_start():
    sim_loop.start()
    return {"running": True}


@app.post("/simulator/stop")
def simulator_stop():
    sim_loop.stop()
    return {"running": False}


@app.get("/simulator/status")
def simulator_status():
    return sim_loop.status()


class SimulatorInjectRequest(BaseModel):
    machine_id: int
    signal: Literal["volt", "rotate", "pressure", "vibration"] | None = None


@app.post("/simulator/inject")
def simulator_inject(req: SimulatorInjectRequest):
    return sim_loop.inject(req.machine_id, req.signal)


@app.post("/simulator/reset")
def simulator_reset():
    sim_loop.reset()
    return {"status": "reset"}



class EventIdsRequest(BaseModel):
    machine_ids: list[int]


@app.get("/events")
def get_events(limit: int = 10):
    return event_store.list_events(limit=limit)

@app.get("/parts/inventory_risk")
def parts_inventory_risk():
    from data.demand_forecast import COMPONENTS, forecast_demand
    from data.parts_operations import check_parts

    demand_30 = forecast_demand(30)
    demand_90 = forecast_demand(90)
    rows = []
    for comp in COMPONENTS:
        parts = check_parts(comp)
        if "error" in parts:
            continue
        on_hand = parts["on_hand"]
        d30, d90 = demand_30[comp], demand_90[comp]
        rows.append({
            "component": comp,
            "part_name": parts["part_name"],
            "on_hand": on_hand,
            "lead_time_days": parts["lead_time_days"],
            "demand_30d": d30,
            "shortfall_30d": max(0, round(d30 - on_hand, 1)),
            "demand_90d": d90,
            "shortfall_90d": max(0, round(d90 - on_hand, 1)),
            "coverable_30d": parts["lead_time_days"] <= 30,
            "coverable_90d": parts["lead_time_days"] <= 90,
            "eol_soon": parts["eol_soon"],
            "eol_status": parts["eol_status"],
            "eol_date": parts["eol_date"],
        })
    return {"rows": rows}

@app.get("/audit_log")
def get_audit_log(
    limit: Annotated[int, Query(ge=1, le=audit_log.MAX_LIMIT)] = 100,
    event_type: str | None = None, thread_id: str | None = None,
    since: str | None = None, until: str | None = None,
):
    return {"events": audit_log.list_events(
        limit=limit, event_type=event_type, thread_id=thread_id, since=since, until=until,
    )}

@app.post("/events/complete")
def complete_events(req: EventIdsRequest):
    count = event_store.complete_events(req.machine_ids)
    return {"completed_count": count}

@app.post("/events/delete")
def delete_events(req: EventIdsRequest):
    count = event_store.delete_events(req.machine_ids)
    return {"deleted_count": count}

