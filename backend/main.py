"""
FastAPI 백엔드

실행 방법:
    uvicorn main:app --reload --port 8000
"""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from openai import OpenAI
from pydantic import BaseModel, field_validator
import logging

from langsmith import Client, traceable
from langsmith.anonymizer import create_anonymizer
from langsmith.wrappers import wrap_openai
from langgraph.checkpoint.sqlite import SqliteSaver


from core.prompts import SYSTEM_PROMPT
from core.harness import (
    validate_input,
    check_output_forbidden_words,
    judge_faithfulness,
    should_retrieve,
    HarnessRejectedError,
    PII_PATTERNS,
)
from rag import rag_service
from agent import agent_service
from data import event_simulator, event_store


load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY가 .env에 설정되어 있지 않습니다.")

# LangSmith로 나가는 트레이스에서 PII를 마스킹 (harness의 PII_PATTERNS 재사용 - 같은 기준으로 응답/트레이스 양쪽 방어)
anonymizer = create_anonymizer([
    {"pattern": pattern.pattern, "replace": f"<{label}>"}
    for label, pattern in PII_PATTERNS.items()
])
langsmith_client = Client(anonymizer=anonymizer)

# 서버 전역에서 재사용할 OpenAI 클라이언트 - wrap_openai로 감싸서 judge 호출까지 자동 추적 + 익명화
client = wrap_openai(
    OpenAI(api_key=OPENAI_API_KEY, timeout=30.0),
    tracing_extra={"client": langsmith_client},
)


# HITL 승인 대기 상태(그래프 체크포인트)를 디스크에 남긴다 - InMemorySaver는 uvicorn --reload나
# 프로세스 재시작마다 대기 중인 승인을 전부 날려버려서 SqliteSaver로 교체함.
CHECKPOINT_DB_PATH = Path(__file__).parent / "store" / "checkpoints.db"
CHECKPOINT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)  # 최초 실행(예: 도커 첫 부팅) 시 store/가 아직 없을 수 있음


@asynccontextmanager
async def lifespan(app: FastAPI):
    rag_service.initialize_rag(OPENAI_API_KEY, DEFAULT_MODEL, langsmith_client)
    event_store.init_event_table()
    with SqliteSaver.from_conn_string(str(CHECKPOINT_DB_PATH)) as checkpointer:
        agent_service.initialize_agent(langsmith_client, checkpointer)
        yield



app = FastAPI(title="AI Agent RAG Backend (OpenAI)", lifespan=lifespan)

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
    return {"status": "ok"}


@app.post("/rag/query", response_model=RAGResponse)
@traceable(name="rag_query", client=langsmith_client)
def rag_query(req: RAGRequest):
    """docs/ 폴더에 적재된 문서를 근거로 질문에 답한다 (RAG)."""
    validate_input(req.question)

    if not should_retrieve(client, req.question):
        completion = client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": req.question},
            ]
        )
        answer = completion.choices[0].message.content
        return RAGResponse(question=req.question, answer=answer, context=None)

    try:
        context, answer = rag_service.answer_with_context(req.question)
    except Exception as e:
        raise LLMAPIError(str(e))

    check_output_forbidden_words(answer)

    faithfulness_result = judge_faithfulness(client, context, answer)
    if not faithfulness_result.get("pass", True):
        raise HarnessRejectedError(
            f"RAG 답변이 검색 문맥에 근거하지 않음(hallucination 의심): {faithfulness_result.get('reason')}"
        )

    return RAGResponse(question=req.question, answer=answer, context=context)



@app.post("/agent/query")
def agent_query(req: AgentQueryRequest):
    """멀티에이전트 그래프에 질문을 보낸다. 긴급 사안이면 승인 대기 상태로 응답할 수 있다."""
    validate_input(req.message)
    return agent_service.start_agent(req.message, req.thread_id)


@app.post("/agent/resume")
def agent_resume(req: AgentResumeRequest):
    """승인 대기 중인 요청에 사람의 결정을 전달해서 재개한다."""
    return agent_service.resume_agent(req.thread_id, req.approved)

@app.post("/simulate/tick")
def simulate_tick(hours: int = 1):
    """실시간 런타임 시뮬레이터: hours시간 분량의 텔레메트리 + 소확률 오류/고장 이벤트를 생성한다."""
    return event_simulator.generate_tick(hours=hours)

@app.post("/scan")
def scan_machines():
    """전체 설비를 스캔해서 긴급/주의로 판정된 설비를 이벤트 저장소에 적재한다."""
    detected = agent_service.scan_all_machines()
    return {"detected_count": len(detected), "events": detected}


class EventIdsRequest(BaseModel):
    machine_ids: list[int]


@app.get("/events")
def get_events(limit: int = 10):
    return event_store.list_events(limit=limit)


@app.post("/events/complete")
def complete_events(req: EventIdsRequest):
    count = event_store.complete_events(req.machine_ids)
    return {"completed_count": count}


@app.post("/events/delete")
def delete_events(req: EventIdsRequest):
    count = event_store.delete_events(req.machine_ids)
    return {"deleted_count": count}

