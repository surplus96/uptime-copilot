"""
하네스 계층: 모델이 제안한 행동(응답)을 검증(validate)하고 실행 여부를 결정한다.
"제안(모델) -> 검증(하네스) -> 실행" 흐름을 라우트 코드와 분리해서 관리.

[OpenAI 호환 버전] project_edu의 Gemini(google-genai) 버전을 OpenAI Python SDK로 포팅.
- google-genai의 client.models.generate_content(config=response_schema=...)
  -> OpenAI의 client.chat.completions.parse(response_format=PydanticModel)로 대체.
  parse()는 Gemini의 response_schema와 동일한 역할(구조화 출력 강제)을 하면서,
  결과를 곧바로 파싱된 Pydantic 인스턴스(message.parsed)로 돌려준다는 점이 다르다.
"""

import logging
import re

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from core import llm_provider

# main.py의 import 순서상 이 모듈이 main.py 자신의 load_dotenv() 호출보다 먼저 로드되므로,
# .env 값을 확실히 읽으려면 이 모듈이 스스로 호출해야 한다(agent_service.py와 동일한 패턴).
load_dotenv()

logger = logging.getLogger(__name__)

MAX_INPUT_LENGTH = 2000
# 단어 목록 대신 "형식"을 잡는 정규식 패턴 (computational check에 적합한 이유: 결정론적으로 판정 가능)
PII_PATTERNS = {
    "주민등록번호": re.compile(r"\d{6}[-\s]?[1-4]\d{6}"),
    "카드번호": re.compile(r"\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}"),
    "전화번호": re.compile(r"01[016789][-\s]?\d{3,4}[-\s]?\d{4}"),
}


class HarnessRejectedError(Exception):
    """하네스 검증을 통과하지 못했을때 발생시키는 예외."""
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


# --------------- Computational checks -------------

def validate_input(message: str) -> None:
    """입력 길이를 검사한다 (computational check)."""
    if len(message) > MAX_INPUT_LENGTH:
        raise HarnessRejectedError(
            f"입력이 너무 깁니다 ({len(message)}자, 최대 {MAX_INPUT_LENGTH}자)"
        )


def check_output_forbidden_words(text: str) -> None:
    """모델 응답에 PII 패턴(주민번호/카드번호/전화번호 형식)이 있는지 검사한다 (computational check)."""
    for label, pattern in PII_PATTERNS.items():
        if pattern.search(text):
            raise HarnessRejectedError(f"응답에 {label} 형식의 문자열이 포함되어 거부되었습니다.")


# --------------- Inferential checks ---------------
JUDGE_MODEL = llm_provider.get_model()

class JudgeResult(BaseModel):
    """LLM-as-judge 출력 구조를 강제하는 스키마. 'pass'는 파이썬 예약어라 alias로 우회."""
    passed: bool = Field(alias="pass")
    score: int | None = None
    reason: str


def _call_judge(client, prompt: str) -> JudgeResult:
    """구조화 출력(response_format)을 강제한 채로 judge를 1회 호출하고 파싱까지 마친다.

    client.chat.completions.parse()는 Gemini의 response_schema와 동일하게
    Pydantic 모델 기준으로 JSON 스키마를 강제하고, 결과를 곧바로
    파싱된 JudgeResult 인스턴스(message.parsed)로 반환한다.
    """
    completion = client.chat.completions.parse(
        model=JUDGE_MODEL,
        reasoning_effort="none",
        messages=[{"role": "user", "content": prompt}],
        response_format=JudgeResult,
    )
    message = completion.choices[0].message
    if message.refusal:
        # OpenAI 구조화 출력은 실패 시 예외 대신 refusal 필드로 알려준다 -> 예외로 변환해
        # 기존 재시도/fail-open 로직(judge_response_quality, judge_faithfulness)이 그대로 처리하게 한다.
        raise ValueError(f"judge 모델이 응답을 거부함: {message.refusal}")
    return message.parsed


FAITHFULNESS_PROMPT_TEMPLATE = """\
당신은 RAG(검색 증강 생성) 시스템의 충실성(faithfulness)을 평가하는 심사관입니다.
[AI 답변]의 모든 주장이 [검색된 문맥]에서 실제로 확인되는지, 아니면 문맥에 없는 내용을 지어냈는지(hallucination) 판단하세요.

# 검색된 문맥
{context}

# AI 답변
{answer}

문맥에 실제로 근거하면 pass: true, 문맥에 없는 내용을 지어냈다면 pass: false로 판정하세요.
반드시 아래 JSON 형식으로만 답하세요.
{{"pass": true 또는 false, "score": 1~5 사이 정수, "reason": "간단한 이유 한 문장"}}
"""


def judge_faithfulness(client, context: str, answer: str) -> dict:
    """RAG 답변이 검색된 문맥에 근거하는지 판정한다 (inferential check, Faithfulness)."""
    prompt = FAITHFULNESS_PROMPT_TEMPLATE.format(context=context, answer=answer)

    try:
        result = _call_judge(client, prompt)
        logger.info(f"Faithfulness 채점 결과: {result}")
        return result.model_dump(by_alias=True)
    except Exception as e:
        first_error = str(e)
        logger.warning(f"Faithfulness judge 1차 실패: {first_error} -> 재시도")

        retry_prompt = (
            prompt
            + f"\n\n[참고] 이전 시도가 실패했습니다 ({first_error}). JSON 스키마를 정확히 지켜서 답하세요."
        )
        try:
            result = _call_judge(client, retry_prompt)
            logger.info(f"Faithfulness 재시도 채점 결과: {result}")
            return result.model_dump(by_alias=True)
        except Exception as e2:
            logger.error(f"Faithfulness judge 재시도도 실패: {e2} -> fail-open")
            return {"pass": True, "score": None, "reason": "faithfulness judge 실패로 스킵(fail-open)"}

