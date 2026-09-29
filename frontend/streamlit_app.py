import streamlit as st
import requests
import uuid
import re
import os
import math
import time


CHAT_AVATARS = {"user": "🧑‍🔧", "assistant": "🛡️"}

st.set_page_config(page_title="Uptime Copilot", page_icon="🛡️", layout="wide")
st.title("Uptime Copilot")

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

# 어느 LLM 프로바이더로 떠 있는지는 세션당 한 번만 조회해서 캐싱한다 - 매 재실행마다
# 다시 호출하면 리런 잦은 Streamlit 특성상 불필요한 지연이 생긴다. 모든 답변이 프로바이더
# 무관하게 동일한 신뢰도로 보이는 문제(interface-reviewer 지적)를 막기 위해 전역으로 노출한다.
if "backend_info" not in st.session_state:
    st.session_state.backend_info = None
if "backend_info_failed_at" not in st.session_state:
    st.session_state.backend_info_failed_at = None
_health_retry_due = (
    st.session_state.backend_info_failed_at is None
    or time.monotonic() - st.session_state.backend_info_failed_at >= 30
)
if st.session_state.backend_info is None and _health_retry_due:
    try:
        st.session_state.backend_info = requests.get(f"{BACKEND_URL}/health", timeout=5).json()
        st.session_state.backend_info_failed_at = None
    except requests.exceptions.RequestException:
        # 2026-09-29 interface-reviewer 지적(B4): 실패를 {}로 캐싱해버리면 다시는
        # 재조회하지 않아서, 오프라인 모드 백엔드인데도 세션 내내 IS_OFFLINE=False로
        # 굳어 "Slack 알림이 발송됩니다" 같은 문구가 계속 잘못 뜰 수 있었다. 실패는
        # 캐싱하지 않고 None으로 남겨서 다음 리런에 다시 시도한다.
        #
        # 2026-09-29 interface-reviewer 후속 지적(R6): 위 방식은 백엔드가 연결 거부가
        # 아니라 아예 응답이 없는 상태(잘못된 호스트 등)일 때, 클릭마다 그리고 사이드바
        # fragment의 주기적 리런마다 매번 최대 5초씩 멈추게 만든다. 실패 시각을 남겨서
        # 30초에 한 번만 재시도한다.
        st.session_state.backend_info = None
        st.session_state.backend_info_failed_at = time.monotonic()

backend_info = st.session_state.backend_info or {}
_provider = backend_info.get("llm_provider")
if _provider:
    _caption = f"🔌 응답 모델: {_provider} / {backend_info.get('llm_model', '?')}"
    if _provider == "ollama":
        _caption += " — 로컬 모델. 분류·추출 정확도가 클라우드 모델보다 낮을 수 있습니다."
    st.sidebar.caption(_caption)
elif st.session_state.backend_info is None:
    st.sidebar.caption("⚠️ 백엔드 상태를 확인하지 못했습니다 — 오프라인 모드 여부와 응답 모델을 알 수 없습니다.")

# 2026-09-29 CP-M2 교차 검토 지적: 화면 어디에도 오프라인 모드 표시가 없어서, 승인
# 화면의 "Slack 알림이 발송됩니다" 같은 문구가 실제로는 거짓인 상태를 사용자가 알
# 방법이 없었다. /health가 이제 offline 상태를 내려주니 사이드바에 항상 보이게 한다.
IS_OFFLINE = bool(backend_info.get("offline"))
if IS_OFFLINE:
    st.sidebar.warning("🔒 오프라인 모드 — Slack 알림·LangSmith 트레이싱이 비활성화됩니다.")


ERROR_LABELS = {
    "llm_api_error": "AI 응답 실패",
    "harness_rejected": "요청 거부됨",
}


def _extract_error_message(exc: requests.exceptions.RequestException) -> str:
    """main.py의 구조화된 에러 응답(harness_rejected 등)이 있으면 그 사유를 보여주고,
    없으면 원본 예외 문자열을 그대로 보여준다. 내부 에러 코드는 한국어 라벨로 바꿔서 표시한다."""
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            body = response.json()
            label = ERROR_LABELS.get(body.get("error"), body.get("error", "오류"))
            if "reason" in body:
                return f"{label}: {body['reason']}"
            if "detail" in body:
                detail = body["detail"]
                if isinstance(detail, list):
                    # FastAPI 422 검증 에러는 detail이 딕셔너리 리스트로 옴
                    detail = "; ".join(d.get("msg", str(d)) for d in detail)
                return f"{label}: {detail}"
        except ValueError:
            pass
    return str(exc)


def parse_work_order(text: str) -> list[dict]:
    """작업지시서 텍스트를 부품별 블록으로 나눠 구조화한다. 형식이 안 맞으면 빈 리스트 반환(폴백용)."""
    if not text:
        return []
    pattern = re.compile(
        r"\[부품\](.*?)\[증상\](.*?)\[매뉴얼 근거\](.*?)\[조치사항\](.*?)\[긴급도\](.*?)\[부품 조달\](.*?)(?=\[부품\]|$)",
        re.DOTALL,
    )
    blocks = []
    for m in pattern.finditer(text):
        blocks.append({
            "부품": m.group(1).strip(),
            "증상": m.group(2).strip(),
            "매뉴얼 근거": m.group(3).strip(),
            "조치사항": m.group(4).strip(),
            "긴급도": m.group(5).strip(),
            "부품 조달": m.group(6).strip(),
        })
    return blocks


def render_work_order(work_order_text: str):
    """부품별로 파싱해서 카드로 보여준다. 파싱 실패 시 원본 텍스트를 그대로 보여주는 폴백.

    2026-09-28 교차 검토 지적: parse_work_order()의 정규식이 첫 [부품] 태그부터만
    캡처해서, 그 앞의 헤더(설비 번호·[우선순위]·[정지 권고]·[근거 관점]·[조달 긴급도])
    가 통째로 버려지고 있었다 - FR-05(우선순위를 부품 문제와 분리)의 유일한 가시
    효과가 바로 이 헤더인데 화면에 전혀 안 보이던 실제 버그."""
    header_end = work_order_text.find("[부품]")
    header = work_order_text[:header_end].strip() if header_end > 0 else ""
    if header:
        st.markdown(header.replace("\n", "  \n"))
        st.divider()

    blocks = parse_work_order(work_order_text)
    if not blocks:
        st.markdown(work_order_text)
        return
    for block in blocks:
        with st.container(border=True):
            st.markdown(f"**🔧 부품: {block['부품']}**  ·  긴급도: `{block['긴급도']}`")
            st.markdown(f"**증상**: {block['증상']}")
            st.markdown(f"**매뉴얼 근거**: {block['매뉴얼 근거']}")
            st.markdown(f"**조치사항**: {block['조치사항']}")
            st.markdown(f"**조달 상태**: {block['부품 조달']}")
    if blocks:
        # 2026-09-29 CP-M2 교차 검토 지적: 재고 위험 탭에만 합성 데이터 고지가 있고,
        # 실제 승인이 이뤄지는 작업지시서/승인 화면에는 없었다 - 부품 조달 상태가
        # 여기 그대로 노출되는데 이 값도 합성 데이터다(§ 재고 위험 탭과 동일 출처).
        st.caption("⚠️ 위 조달 상태는 합성(가상) 부품 데이터 기준입니다 — 재고 위험 탭과 같은 고지.")



tab1, tab2, tab3, tab4, tab5 = st.tabs(["설비 에이전트", "매뉴얼 검색", "이상감지 이벤트", "재고 위험", "감사 로그"])

# ---------- 설비 에이전트 모드 ----------
with tab1:
    if "agent_thread_id" not in st.session_state:
        st.session_state.agent_thread_id = str(uuid.uuid4())
    if "agent_messages" not in st.session_state:
        st.session_state.agent_messages = []
    if "pending_approval" not in st.session_state:
        st.session_state.pending_approval = None
    if "timeout_recovery" not in st.session_state:
        st.session_state.timeout_recovery = None
    if "resume_error" not in st.session_state:
        st.session_state.resume_error = None

    # 이 사이드바 컨트롤은 st.sidebar가 전역이라 다른 탭을 보고 있을 때도 그대로 뜬다 -
    # 어느 탭 소속인지 라벨로 명시한다 (interface-reviewer 지적, 2026-09-18).
    st.sidebar.markdown("**🤖 설비 에이전트**")
    reset_confirmed = True
    if st.session_state.pending_approval:
        reset_confirmed = st.sidebar.checkbox(
            "⚠️ 승인 대기 중인 항목을 버리고 새 진단을 시작합니다",
        )
    if st.sidebar.button("새 진단 시작", disabled=not reset_confirmed):
        st.session_state.agent_thread_id = str(uuid.uuid4())
        st.session_state.agent_messages = []
        st.session_state.pending_approval = None
        st.session_state.timeout_recovery = None
        st.session_state.resume_error = None
        st.rerun()

    if not st.session_state.agent_messages and not st.session_state.pending_approval:
        with st.container(border=True):
            st.markdown("### 👋 무엇을 도와드릴까요?")
            st.markdown(
                "설비 번호와 증상을 알려주시면, 실제 이력 데이터를 바탕으로 진단하고 "
                "필요시 매뉴얼을 참고해 작업지시서 초안까지 작성해드립니다."
            )
            st.markdown("**예시 질문으로 시작해보세요:**")
            example_questions = [
                "64번 설비에서 오류 났는데 뭐가 문제야?",
                "5번 설비 상태 확인해줘",
                "1번 설비 다음 점검은 언제야?",
            ]
            cols = st.columns(len(example_questions))
            for col, q in zip(cols, example_questions):
                if col.button(q, use_container_width=True):
                    st.session_state.pending_example = q
                    st.rerun()

    for message in st.session_state.agent_messages:
        with st.chat_message(message["role"], avatar=CHAT_AVATARS.get(message["role"])):
            if message["role"] == "assistant" and message.get("work_order"):
                content = message["content"]
                if content.startswith("[긴급 승인됨]"):
                    # 2026-09-29 interface-reviewer 지적(A3): 오프라인·미설정 상태에서도
                    # "Slack 알림과 CMMS 등록을 자동 처리했습니다"라고 단정했다 - 실제로는
                    # 오프라인이면 Slack은 스킵되고(blocked_by_offline), 웹훅이 없으면
                    # 아무 기록도 없이 조용히 반환되며, CMMS도 skipped_unconfigured/
                    # blocked_insecure_url/blocked_offline일 수 있다. "시도했다"까지만
                    # 확정하고, 실제 결과는 감사 로그로 안내한다.
                    st.success("✅ 승인됨 — 서버가 Slack 알림과 CMMS 작업지시서 등록을 시도했습니다(설정된 경우).")
                    thread_id = message.get("thread_id")
                    if thread_id:
                        st.caption(
                            f"실제 전송 결과(성공/실패/오프라인 차단/미설정 스킵)는 '감사 로그' 탭에서 "
                            f"스레드 ID로 확인하세요: `{thread_id}`"
                        )
                    else:
                        st.caption("실제 전송 결과는 '감사 로그' 탭에서 확인하세요.")
                elif content.startswith("[긴급 반려됨]"):
                    st.warning("🚫 반려됨 — 별도 조치는 이루어지지 않았습니다.")
                render_work_order(message["work_order"])
                with st.expander("원본 응답 텍스트"):
                    st.text(content)
            else:
                st.markdown(message["content"])

    if st.session_state.pending_approval:
        # 다른 assistant 메시지는 전부 아바타가 붙는데 이 블록만 chat_message 밖이라
        # 아바타 없이 렌더링되던 걸 고침 - 재들여쓰기 없이 with 튜플로 감싼다
        # (interface-reviewer 지적, 2026-09-18).
        with st.chat_message("assistant", avatar=CHAT_AVATARS["assistant"]), st.container(border=True):
            st.warning("⚠️ 승인이 필요합니다 — 아래 작업지시서를 검토하세요")
            # 2026-09-29 CP-M2 교차 검토 지적: 오프라인 모드에서도 이 문구가 그대로
            # "Slack 알림이 발송됩니다"라고 떠서 실제로는 차단되는 동작을 거짓으로
            # 안내했다 - 오프라인이면 그 사실을 그대로 알린다.
            #
            # 2026-09-29 interface-reviewer 지적(A4): 이후에도 두 문구 모두 "확정적으로"
            # 말했지만, Slack 웹훅이나 CMMS 주소가 애초에 설정 안 됐으면 둘 다 거짓이다
            # (/health가 그 설정 여부까지는 안 내려주므로 여기서 확정할 수 없음) - "시도한다"
            # 정도로 주장을 낮추고, 오프라인 쪽 CMMS 문구도 "루프백 주소로 설정된 경우에만"
            # 이라는 조건을 명시한다.
            if IS_OFFLINE:
                st.caption("🔒 오프라인 모드 — 승인해도 Slack 알림은 발송되지 않습니다. CMMS는 "
                           "내부(루프백) 주소로 설정된 경우에만 등록을 시도합니다. 반려하면 아무 것도 전송되지 않습니다.")
            else:
                st.caption("승인하면 서버가 Slack 긴급 채널 알림과 CMMS 긴급(HIGH) 작업지시서 등록을 "
                           "시도합니다(설정된 경우). 반려하면 아무 것도 전송되지 않습니다.")
            wo = st.session_state.pending_approval.get("work_order")
            if wo:
                render_work_order(wo)
            else:
                st.markdown(st.session_state.pending_approval["message"])
            # 2026-09-29 interface-reviewer 지적(E1): 감사 로그 탭에 스레드ID 필터를
            # 추가했는데, 정작 그 ID를 화면 어디서도 안 보여줘서 실제로 쓸 방법이
            # 없었다 - 복사해서 필터에 붙여넣을 수 있게 여기서도 노출한다.
            st.caption(f"스레드 ID: `{st.session_state.agent_thread_id}`")

            PERSPECTIVE_ORDER = ["안전", "생산", "정비"]
            ICONS = {"안전": "🛡️", "생산": "🏭", "정비": "🛠️"}

            perspectives = st.session_state.pending_approval.get("perspectives") or []
            parsed = {}
            for p in perspectives:
                label, sep, text = p.partition("] ")
                label = label.lstrip("[") if sep else ""
                parsed.setdefault(label, (text or p).strip())

            if any(parsed.get(n) for n in PERSPECTIVE_ORDER):
                st.markdown("**🤖 3개 관점 AI 의견** (참고용 — 매뉴얼로 검증된 작업지시서와 달리 근거 확인 없이 생성됨)")
                cols = st.columns(len(PERSPECTIVE_ORDER))
                for col, name in zip(cols, PERSPECTIVE_ORDER):
                    with col:
                        with st.container(border=True):
                            st.markdown(f"**{ICONS[name]} {name} 관점**")
                            text = parsed.get(name)
                            st.caption(text if text and text != "None" else "의견을 가져오지 못했습니다.")
            else:
                st.caption("⚠️ 안전·생산·정비 3개 관점 의견을 불러오지 못했습니다. 승인 전에 아래 '원본 메시지 보기'를 확인하세요.")
            
            with st.expander("원본 메시지 보기"):
                st.text(st.session_state.pending_approval["message"])

            # layout="wide" 이후 col1/col2 50:50 분할이 화면 절반 크기 버튼이 됐다 -
            # 실제 Slack/CMMS에 쓰기 작업을 트리거하는 버튼치고 너무 큰 클릭 타겟이라
            # 실수 클릭 위험이 있었다 (interface-reviewer 지적, 2026-09-18).
            col1, col2, _ = st.columns([1, 1, 4])

            def _resume(approved: bool):
                try:
                    res = requests.post(
                        f"{BACKEND_URL}/agent/resume",
                        json={"thread_id": st.session_state.agent_thread_id, "approved": approved},
                        timeout=60,
                    )
                    res.raise_for_status()
                    data = res.json()
                except requests.exceptions.RequestException as e:
                    # 타임아웃/네트워크 오류만으로는 서버가 실제로 처리했는지 알 수 없다 -
                    # 여기서 pending_approval을 지우면 실제로는 이미 성공(Slack+CMMS까지
                    # 나간)했는데 사용자에게는 "실패"로 보이고 재시도할 방법도 사라진다
                    # (code-quality-reviewer + interface-reviewer 공통 지적, 2026-09-18).
                    # 확실해질 때까지 승인 대기 상태를 그대로 유지한다.
                    #
                    # 2026-09-29 interface-reviewer 지적(B1, 가장 심각): 이 st.error()는
                    # 이 함수 호출 시점에만 한 번 그려지는데, 사이드바 이벤트 스캐너
                    # fragment가 10초마다 앱 전체를 리런시키면(기본 scope="app") 그
                    # 리런에서는 이 코드가 다시 실행되지 않으니 에러가 조용히 사라진다 -
                    # 마치 아무 일도 없었던 것처럼 승인 카드만 남는다. session_state로
                    # 옮겨서 pending_approval이 남아있는 한 계속 보이게 한다.
                    st.session_state.resume_error = (
                        f"승인 결과를 확인하지 못했습니다: {_extract_error_message(e)}\n\n"
                        "요청이 서버에 전달되어 이미 처리됐을 수도 있습니다. 다시 누르기 전에 "
                        "Slack 채널이나 CMMS에서 작업지시서가 이미 등록됐는지 확인하세요."
                    )
                    return
                st.session_state.resume_error = None
                st.session_state.agent_messages.append({
                    "role": "assistant", "content": data["result"], "work_order": data.get("work_order"),
                    "thread_id": st.session_state.agent_thread_id,
                })
                st.session_state.pending_approval = None
                st.rerun()

            if st.session_state.get("resume_error"):
                st.error(st.session_state.resume_error)

            if col1.button("승인", use_container_width=True):
                _resume(True)
            if col2.button("반려", use_container_width=True):
                _resume(False)
    elif st.session_state.timeout_recovery:
        # session_state 기반이라 prompt와 무관하게 리런에서도 계속 렌더링된다 -
        # 위 except 블록의 주석 참고.
        with st.chat_message("assistant", avatar=CHAT_AVATARS["assistant"]), st.container(border=True):
            recovery = st.session_state.timeout_recovery
            st.warning(
                f"응답을 받지 못했습니다: {recovery['error_msg']}\n\n"
                "서버에서는 계속 처리 중이었을 수 있습니다. 아래 버튼으로 이 설비 요청이 "
                "실제로 처리됐는지 확인하세요."
            )
            col1, col2 = st.columns(2)
            if col1.button("이 요청 상태 확인", use_container_width=True):
                # 2026-09-29 interface-reviewer 지적(A5): 상태 확인 요청 자체가
                # 실패해도 곧바로 "아직 처리 중입니다"로 떨어져서, 에러와 "처리 중"
                # 안내가 동시에(모순되게) 뜨고 있었다. 요청이 실패하면 거기서 끝내고
                # "처리 중"이라고 단정하지 않는다. st.stop()은 다른 탭까지 멈추므로
                # (code-quality-reviewer 지적, 2026-09-18) 안 쓰고 플래그로 분기한다.
                check_ok = True
                check: dict = {}
                try:
                    res = requests.get(f"{BACKEND_URL}/agent/status/{recovery['thread_id']}", timeout=10)
                    res.raise_for_status()
                    check = res.json()
                except requests.exceptions.HTTPError as e:
                    # check_pending()이 이제 _validate_output()을 거치므로(2026-09-29
                    # 수정), 복구하려던 원래 응답이 PII 등으로 거부됐을 수도 있다 -
                    # 이 경우는 "아직 처리 중"이 아니라 확정적으로 표시할 수 없는
                    # 결과다.
                    #
                    # 2026-09-29 interface-reviewer 후속 지적(R3, B1과 같은 종류): 여기서
                    # st.error()만 그리고 timeout_recovery를 지우면, 이 에러는 이번 리런에만
                    # 보이고 다음 리런(다른 클릭이나 사이드바 fragment의 앱 전체 리런)에
                    # 흔적 없이 사라진다 - 질문은 응답 없이 기록에 남는다. 쿼리 실패 때와
                    # 같이 채팅 기록(session_state)에 영구히 남긴다.
                    st.session_state.agent_messages.append({
                        "role": "assistant",
                        "content": f"⚠️ 이 요청의 결과를 표시할 수 없습니다 — {_extract_error_message(e)}",
                    })
                    st.session_state.timeout_recovery = None
                    check_ok = False
                    st.rerun()
                except requests.exceptions.RequestException as e:
                    st.error(f"상태 확인 요청 자체가 실패했습니다: {_extract_error_message(e)}")
                    check_ok = False

                if not check_ok:
                    pass
                elif check.get("status") == "pending_approval":
                    st.session_state.pending_approval = {
                        "message": check["message"],
                        "work_order": check.get("work_order"),
                        "perspectives": check.get("perspectives", []),
                    }
                    st.session_state.timeout_recovery = None
                    st.rerun()
                elif check.get("status") == "done":
                    # 2026-09-29 수정: 긴급이 아닌 요청(일반/주의)은 애초에 승인
                    # 대기를 안 거치므로, 이미 끝났는데도 "not_found"로만 보이던
                    # 문제 - check_pending()이 이제 완료 결과도 복구해준다.
                    st.session_state.agent_messages.append({
                        "role": "assistant",
                        "content": check["result"],
                        "work_order": check.get("work_order"),
                    })
                    st.session_state.timeout_recovery = None
                    st.rerun()
                else:
                    st.info(
                        "아직 결과가 없습니다. 서버가 계속 처리 중이거나, 요청이 도중에 실패해 "
                        "결과가 남지 않았을 수 있습니다. 1~2분 뒤에도 같다면 '포기하고 새로 "
                        "시작'으로 다시 질문하세요."
                    )
            if col2.button("포기하고 새로 시작", use_container_width=True):
                st.session_state.timeout_recovery = None
                st.rerun()
    else:
        prompt = st.chat_input("설비 번호와 증상을 입력하세요 (예: 12번 설비에서 오류 났는데 뭐가 문제야?)")
        if not prompt and "pending_example" in st.session_state:
            prompt = st.session_state.pop("pending_example")

        if prompt and prompt.strip():
            thread_id = str(uuid.uuid4())  # 질문마다 새 스레드 - 이전 진단 상태가 새 질문에 남지 않게
            st.session_state.agent_thread_id = thread_id  # 승인 대기 시 재사용하기 위해 저장

            st.session_state.agent_messages.append({"role": "user", "content": prompt})
            with st.chat_message("user", avatar=CHAT_AVATARS["user"]):
                st.markdown(prompt)


            with st.spinner("진단 중..."):
                # st.stop()은 스크립트 전체(다른 탭까지)를 멈춘다 - 이 탭의 요청 실패는
                # 이 탭 안에서만 처리해야 한다 (code-quality-reviewer 지적, 2026-09-18).
                request_ok = True
                try:
                    res = requests.post(
                        f"{BACKEND_URL}/agent/query",
                        json={"message": prompt, "thread_id": thread_id},
                        timeout=60,
                    )
                    res.raise_for_status()
                    data = res.json()
                except requests.exceptions.ConnectionError:
                    # 서버에 요청 자체가 닿지 않았다 - "처리 중일 수 있다"는 말이 거짓이므로
                    # 복구 패널이 아니라 확정적인 실패로 채팅 기록에 남긴다(채팅 기록은
                    # session_state 기반이라 리런에도 살아남는다 - prompt 의존 문제 없음).
                    #
                    # 2026-09-29 interface-reviewer 후속 지적(R5): ConnectTimeout은
                    # Timeout과 ConnectionError 둘 다의 하위클래스라, 아래 except Timeout이
                    # 먼저였을 때는 "연결 자체가 안 됨"인 경우도 전부 "처리 중일 수
                    # 있습니다" 복구 패널로 갔다. ConnectionError를 먼저 잡아야
                    # ConnectTimeout이 여기로 오고, 순수 ReadTimeout(ConnectionError의
                    # 하위클래스 아님)만 아래 Timeout 절로 간다.
                    st.session_state.agent_messages.append({
                        "role": "assistant",
                        "content": "⚠️ 백엔드 서버에 연결하지 못했습니다. 요청은 전송되지 않았습니다. "
                                   "서버 실행 여부를 확인한 뒤 다시 질문하세요.",
                    })
                    request_ok = False
                except requests.exceptions.Timeout:
                    # 2026-09-29 실사용 중 발견한 버그: 이 자리에서 바로 경고+버튼을
                    # 그렸었는데, 이 블록 전체가 st.chat_input()의 반환값(prompt)에
                    # 의존하고 있어서 - prompt는 메시지를 보낸 바로 그 리런에만 값이
                    # 있고 그다음 리런(버튼 클릭 포함)부터는 다시 비어있다. 그래서
                    # 버튼을 누르면 화면이 다시 그려지면서 이 블록 자체가 사라져
                    # 클릭이 처리될 기회조차 없었다. session_state로 옮겨서 리런에도
                    # 살아남게 한다 - 실제 렌더링은 아래 elif 블록에서.
                    #
                    # 2026-09-29 interface-reviewer 지적(A2): 예전엔 RequestException
                    # 하나로 타임아웃/연결실패/서버거부(4xx)/서버오류(5xx)를 전부
                    # 뭉뚱그려서 "서버에서는 계속 처리 중이었을 수 있습니다"라고 했다 -
                    # 연결 실패나 확정적 거부는 "처리 중"일 수가 없는데도 같은 복구
                    # 패널을 띄웠다. 타임아웃만 진짜로 재시도할 가치가 있으므로 분리한다.
                    # 2026-09-29 interface-reviewer 후속 지적(R4): Timeout엔 .response가
                    # 없어서 _extract_error_message가 원본 예외 문자열(HTTPConnectionPool
                    # 등 내부 구현 용어)을 그대로 반환했고, 이걸 렌더링할 때 "응답을 받지
                    # 못했습니다: "를 한 번 더 붙여 같은 말이 두 번 나왔다. 고정 문구만 쓴다.
                    st.session_state.timeout_recovery = {
                        "thread_id": thread_id,
                        "error_msg": "60초 안에 응답이 오지 않았습니다.",
                    }
                    request_ok = False
                except requests.exceptions.HTTPError as e:
                    # 서버가 응답은 했지만 확정적으로 거부한 경우(400 harness_rejected,
                    # 422 입력 검증 실패, 5xx 등) - 재시도해도 같은 결과이므로 복구
                    # 패널이 아니라 확정적인 실패로 표시한다.
                    st.session_state.agent_messages.append({
                        "role": "assistant",
                        "content": f"⚠️ 요청이 처리되지 않았습니다 — {_extract_error_message(e)}",
                    })
                    request_ok = False
                except requests.exceptions.RequestException as e:
                    # 위 세 가지로 분류 안 되는 나머지 - 안전한 쪽(복구 패널)으로 보낸다.
                    st.session_state.timeout_recovery = {
                        "thread_id": thread_id,
                        "error_msg": _extract_error_message(e),
                    }
                    request_ok = False

            if request_ok:
                if data["status"] == "pending_approval":
                    st.session_state.pending_approval = {
                        "message": data["message"],
                        "work_order": data.get("work_order"),
                        "perspectives": data.get("perspectives", []),
                    }

                else:
                    st.session_state.agent_messages.append(
                        {"role": "assistant", "content": data["result"], "work_order": data.get("work_order")}
                    )
            st.rerun()  # 성공/실패 어느 쪽이든 리런해서 pending_approval/timeout_recovery
                        # 분기가 각자의 지속 상태로 정상 렌더링되게 한다


# ---------- 매뉴얼 검색 모드 ----------
with tab2:
    st.subheader("📄 정비 매뉴얼 검색")
    st.caption("예: \"comp3 부품은 어떤 증상과 관련있어?\", \"error5는 무슨 뜻이야?\"")

    query = st.text_input("궁금한 내용을 입력하세요")
    search_clicked = st.button("검색")
    if search_clicked and not query.strip():
        st.warning("검색어를 입력해주세요.")
    elif search_clicked:
        with st.spinner("검색 중..."):
            try:
                res = requests.post(f"{BACKEND_URL}/rag/query", json={"question": query}, timeout=60)
                res.raise_for_status()
                # session_state에 저장해서 다른 위젯 클릭으로 재실행돼도 결과가 안 사라지게
                # 한다 - search_clicked는 버튼 누른 바로 다음 실행에서만 True라서, 이 값에
                # 의존해 렌더링하면 그 즉시 다음 재실행에 결과가 사라졌었다
                # (code-quality-reviewer 지적, 2026-09-18).
                st.session_state.rag_result = res.json()
            except requests.exceptions.HTTPError as e:
                # 2026-09-29 interface-reviewer 후속 지적(R1/R2): A6에서 harness_rejected를
                # 전부 "매뉴얼 근거와 안 맞음"으로 안내했는데, 실제로는 입력 길이 초과·PII
                # 포함 등 다른 사유도 같은 코드로 온다 - 원인을 추측해 잘못 안내하면 사용자가
                # 반대 방향으로 질문을 고치게 된다. 서버가 준 reason을 그대로 보여준다
                # (R1). 또한 e.response.json()을 직접 호출하면 응답 본문이 JSON이 아닐 때
                # (프록시 502 등) JSONDecodeError로 이 블록 자체가 죽어 탭 전체 렌더링이
                # 멈췄다 - 이미 안전하게 파싱하는 _extract_error_message를 재사용한다(R2).
                if e.response is not None and e.response.status_code == 400:
                    st.warning(f"답변을 표시하지 않았습니다 — {_extract_error_message(e)}")
                else:
                    st.error(f"백엔드 요청 실패: {_extract_error_message(e)}")
            except requests.exceptions.RequestException as e:
                st.error(f"백엔드 요청 실패: {_extract_error_message(e)}")

    rag_result = st.session_state.get("rag_result")
    if rag_result:
        st.markdown(f"**답변**\n\n{rag_result['answer']}")
        if rag_result.get("verified") is False:
            reason = rag_result.get("verified_reason") or "채점 결과를 구조화된 형식으로 받지 못함"
            st.warning(
                f"⚠️ 이 답변은 출처 문맥과 일치하는지 자동 검증되지 못했습니다 ({reason}) — 내용을 직접 확인해 주세요."
            )
        # "검증됨"이 독립 기관의 확인처럼 읽히지 않도록, 답변을 생성한 것과 같은 모델이
        # 스스로 채점한다는 사실을 항상 명시한다 (interface-reviewer 지적).
        st.caption("검증은 답변을 생성한 것과 같은 모델이 스스로 채점한 결과이며, 독립적인 검증이 아닙니다.")
        if rag_result.get("context"):
            with st.expander("📄 참고한 매뉴얼 원문 보기"):
                st.text(rag_result["context"])

_SIGNAL_LABELS = {"volt": "전압", "rotate": "회전", "pressure": "압력", "vibration": "진동"}
_STATE_LABELS = {"DEGRADING": "열화 중", "FAULT": "전조 오류 발생"}


@st.fragment(run_every="10s")
def _simulator_panel():
    try:
        res = requests.get(f"{BACKEND_URL}/simulator/status", timeout=10)
        res.raise_for_status()
        status = res.json()
    except requests.exceptions.RequestException as e:
        with st.container(border=True):
            st.error("백엔드에 연결하지 못했습니다. 서버가 실행 중인지 확인한 뒤 다시 시도하세요.")
            st.caption(f"요청 주소: {BACKEND_URL}/simulator/status · 10초 후 자동 재시도")
            with st.expander("자세한 오류"):
                st.code(_extract_error_message(e))
        return

    with st.container(border=True):
        col1, col2, col3 = st.columns([2, 2, 1])
        running = status["running"]
        col1.markdown(f"{'▶️ 실행 중' if running else '⏸️ 정지'} · 시뮬레이션 시각: `{status['sim_now'] or '없음'}`")

        speed = f"속도: 실제 {status['tick_seconds']}초 = 시뮬레이션 {status['hours_per_tick']}시간"
        if running and status["elapsed_seconds"] is not None:
            h, rem = divmod(status["elapsed_seconds"], 3600)
            m, s = divmod(rem, 60)
            speed += f" · 실행 경과 {f'{h}시간 ' if h else ''}{m}분 {s}초"
        col2.caption(speed)

        if running:
            if col3.button("정지", key="sim_stop"):
                try:
                    r = requests.post(f"{BACKEND_URL}/simulator/stop", timeout=10)
                    r.raise_for_status()
                    st.rerun()
                except requests.exceptions.RequestException as e:
                    st.error(f"시뮬레이터를 정지하지 못했습니다: {_extract_error_message(e)}")
        else:
            if col3.button("시작", key="sim_start"):
                try:
                    r = requests.post(f"{BACKEND_URL}/simulator/start", timeout=10)
                    r.raise_for_status()
                    st.rerun()
                except requests.exceptions.RequestException as e:
                    st.error(f"시뮬레이터를 시작하지 못했습니다: {_extract_error_message(e)}")

        since = status["seconds_since_last_tick"]
        if running and since is not None:
            if since > status["tick_seconds"] * 2:
                st.warning(f"마지막 진행 이후 {since}초째 갱신이 없습니다. 아래 오류 내용을 확인하세요.")
            else:
                st.caption(f"⏳ 다음 진행까지 약 {max(0, status['tick_seconds'] - since)}초 (10초마다 이 화면 자동 갱신)")

        if status.get("last_error"):
            st.error(f"최근 진행 {status.get('consecutive_failures', 0)}회 연속 실패: {status['last_error']}")

        if status["has_stale_events"]:
            st.info(f"감지 목록에 항목이 있습니다. 시뮬레이터 상태와는 별개로 유지되며, 이전 실행이나 수동 전체 스캔의 결과일 수 있습니다.")

        col_r1, col_r2 = st.columns([3, 1])
        col_r1.caption(
            "리셋: 시뮬레이션 데이터, 감지 목록, **완료 처리 이력**을 모두 삭제하고 시뮬레이터를 정지합니다. "
            "되돌릴 수 없으며, 수동 전체 스캔 결과와 완료 처리한 항목도 함께 사라집니다."
        )
        reset_ok = col_r1.checkbox("⚠️ 위 내용을 확인했습니다", key="sim_reset_confirm")
        if col_r2.button("리셋", key="sim_reset", disabled=not reset_ok, type="secondary"):
            try:
                r = requests.post(f"{BACKEND_URL}/simulator/reset", timeout=10)
                r.raise_for_status()
                st.session_state.pop("sim_event_count", None)
                st.session_state.pop("sim_reset_confirm", None)
                st.rerun()
            except requests.exceptions.RequestException as e:
                st.error(f"리셋하지 못했습니다: {_extract_error_message(e)}")

        degrading = status["degrading"]
        if degrading:
            st.caption(
                f"열화 진행 중인 설비 {len(degrading)}대 — 감지 기준에 도달하면 아래 이벤트 목록에 나타나고, "
                "고장이 기록된 설비는 이 목록에서 사라집니다."
            )
            for d in sorted(degrading, key=lambda x: -x["progress"])[:8]:
                signal_label = _SIGNAL_LABELS.get(d["signal"], d["signal"] or "?")
                state_label = _STATE_LABELS.get(d["state"], d["state"])
                pct = round(d["progress"] * 100)
                label = f"설비 #{d['machine_id']} · {signal_label} 이상 · {state_label} · 예상 고장 시점의 {pct}% 경과"
                if d["errors_emitted"]:
                    label += f" · 전조 오류 {d['errors_emitted']}건"
                st.progress(min(d["progress"], 1.0), text=label)
            if len(degrading) > 8:
                st.caption(f"…외 {len(degrading) - 8}대")
        else:
            st.caption("현재 열화 진행 중인 설비 없음")

    try:
        ev_res = requests.get(f"{BACKEND_URL}/events", params={"limit": 1}, timeout=10)
        ev_res.raise_for_status()
        current_total = ev_res.json()["total"]
    except requests.exceptions.RequestException:
        current_total = st.session_state.get("sim_event_count")

    if current_total is not None and current_total != st.session_state.get("sim_event_count"):
        st.session_state.sim_event_count = current_total
        st.rerun()


# ---------- 이상감지 이벤트 모드 ----------
with tab3:
    st.subheader("🔔 감지된 이상 이벤트")
    _simulator_panel()
    st.divider()
    
    if "event_feedback" in st.session_state:
        st.success(st.session_state.pop("event_feedback"))

    if st.button("지금 전체 스캔하기"):
        with st.spinner("100대 설비 스캔 중..."):
            scan_ok = True
            try:
                res = requests.post(f"{BACKEND_URL}/scan", timeout=120)
                res.raise_for_status()
                detected_count = res.json()["detected_count"]
            except requests.exceptions.RequestException as e:
                st.error(f"백엔드 요청 실패: {_extract_error_message(e)}")
                scan_ok = False
        if scan_ok:
            st.session_state.has_scanned = True
            st.session_state.event_feedback = (
                f"스캔 완료: {detected_count}건 발견" if detected_count else "스캔 완료: 이상 없음"
            )
            st.rerun()

    # 아래 이벤트 목록 전체가 이 요청 성공에 의존한다 - st.stop()으로 스크립트 전체를
    # 멈추면 이미 렌더링된 다른 탭까지는 영향 없지만(tab3가 마지막이라), 실패 시 이 탭
    # 안에서만 건너뛰도록 일관되게 플래그로 처리한다 (code-quality-reviewer 지적, 2026-09-18).
    events_ok = True
    try:
        res = requests.get(f"{BACKEND_URL}/events", params={"limit": 10}, timeout=30)
        res.raise_for_status()
        data = res.json()
        events = data["events"]
        total = data["total"]
    except requests.exceptions.RequestException as e:
        st.error(f"백엔드 요청 실패: {_extract_error_message(e)}")
        events_ok = False

    if events_ok:
        if total > len(events):
            st.caption(f"전체 {total}건 중 긴급/최신순 상위 {len(events)}건만 표시합니다.")

        if not events:
            if st.session_state.get("has_scanned"):
                st.info("스캔 결과 감지된 이벤트가 없습니다.")
            else:
                st.info("아직 스캔한 적이 없습니다. 위 '지금 전체 스캔하기'를 눌러 확인하세요.")
        else:
            col_all1, col_all2 = st.columns(2)
            select_all = col_all1.button("표시된 항목 전체 선택")
            clear_all = col_all2.button("표시된 항목 전체 해제")

            for ev in events:
                key = f"chk_{ev['machine_id']}"
                if select_all:
                    st.session_state[key] = True
                if clear_all:
                    st.session_state[key] = False
                with st.container(border=True):
                    badge = "🔴" if ev["severity"] == "긴급" else "🟡"
                    col1, col2 = st.columns([1, 9])
                    col1.checkbox("선택", key=key, label_visibility="collapsed")
                    # detected_at은 근거 발생 시각이 아니라 이 스캔 버튼을 누른 시각(벽시계)이다.
                    col2.markdown(f"{badge} **설비 #{ev['machine_id']}** · {ev['severity']} · 마지막 스캔: {ev['detected_at']}")
                    col2.caption(ev["diagnosis"])

            selected = [ev["machine_id"] for ev in events if st.session_state.get(f"chk_{ev['machine_id']}", False)]

            st.divider()
            st.caption(
                "완료 처리: 이 목록에서만 숨기고 설비 상태 자체는 바뀌지 않습니다 (새 근거가 생기면 재등장). "
                "삭제: 기록을 남기지 않아, 같은 조건이면 다음 스캔에 바로 다시 나타날 수 있습니다."
            )
            col_a, col_b = st.columns(2)
            if col_a.button(f"완료 처리 ({len(selected)}건)", disabled=not selected):
                complete_ok = True
                try:
                    res = requests.post(f"{BACKEND_URL}/events/complete", json={"machine_ids": selected}, timeout=30)
                    res.raise_for_status()
                    completed_count = res.json()["completed_count"]
                except requests.exceptions.RequestException as e:
                    st.error(f"처리 실패: {_extract_error_message(e)}")
                    complete_ok = False
                if complete_ok:
                    st.session_state.event_feedback = f"{completed_count}건 완료 처리했습니다."
                    st.rerun()
            if col_b.button(f"선택 삭제 ({len(selected)}건)", disabled=not selected):
                delete_ok = True
                try:
                    res = requests.post(f"{BACKEND_URL}/events/delete", json={"machine_ids": selected}, timeout=30)
                    res.raise_for_status()
                    deleted_count = res.json()["deleted_count"]
                except requests.exceptions.RequestException as e:
                    st.error(f"삭제 실패: {_extract_error_message(e)}")
                    delete_ok = False
                if delete_ok:
                    st.session_state.event_feedback = f"{deleted_count}건 삭제했습니다."
                    st.rerun()


@st.cache_data(ttl=300)
def _fetch_inventory_risk():
    res = requests.get(f"{BACKEND_URL}/parts/inventory_risk", timeout=30)
    res.raise_for_status()
    return res.json()["rows"]


with tab4:
    st.subheader("📦 부품 재고 위험")
    st.caption("예상 수요(30/90일)가 현재 재고를 초과하는 부품을 보여줍니다. 리드타임이 예측 기간보다 길면 지금 발주해도 늦을 수 있습니다.")
    # 2026-09-28 교차 검토 지적(H3 규칙 6 - 합성 데이터 명시 의무): 부품 마스터가
    # 전부 합성값이고(docs/design/parts_assumptions.md), 수요는 과거 교체율 기반
    # 추정치라 이미 발주해둔 물량은 반영하지 못한다는 걸 화면에서 밝혀야 한다.
    st.caption("⚠️ 부품 재고·리드타임은 합성(가상) 데이터입니다. 수요는 과거 교체 이력 기반 추정치이며, 이미 발주해 입고 대기 중인 물량은 반영하지 않습니다.")
    try:
        rows = _fetch_inventory_risk()
    except requests.exceptions.RequestException as e:
        st.error(f"백엔드 요청 실패: {_extract_error_message(e)}")
        rows = []

    for r in rows:
        with st.container(border=True):
            title = f"{r['component']} - {r['part_name']}"
            # 2026-09-29 CP-M2 교차 검토 지적: eol_soon만 보고 항상 "단종 임박"이라고
            # 표시했다 - 실제로는 eol_status가 "경과"(이미 단종일이 지남)인 경우도
            # 똑같이 "임박"으로 나와서, 이미 단종된 부품을 아직 여유 있다는 듯 오독하게
            # 만들었다. eol_status를 그대로 반영한다.
            if r["eol_status"] == "경과":
                title += " ⚠️ 단종됨(단종일 경과)"
            elif r["eol_status"] == "임박":
                title += " ⚠️ 단종 임박"
            st.markdown(f"**{title}**")
            c1, c2, c3 = st.columns(3)
            c1.metric("현재 재고", r["on_hand"])
            c2.metric("30일 예상수요", f"약 {round(r['demand_30d'])}개")
            c3.metric("90일 예상수요", f"약 {round(r['demand_90d'])}개")
            # 2026-09-28 문서 정합성 3차 점검 지적 + 사용자 결정: 90일 기준 부족분은
            # 리드타임 안에 조달 가능하면(coverable) "정상 재주문 신호"이지 comp4류의
            # 실제 위험(30일 안에도 조달이 안 맞는 경우)과 같은 급이 아니다 - 지금까지는
            # 둘 다 비슷한 톤으로 떠서 화면만 보면 구분이 안 됐다. coverable 여부로
            # 위험(st.warning)과 정상 재주문 시점(st.caption)을 시각적으로 분리한다.
            if r["shortfall_30d"] > 0:
                warn = f"⚠️ 30일 내 약 {math.ceil(r['shortfall_30d'])}개 부족 예상"
                if not r["coverable_30d"]:
                    warn += f" (리드타임 {r['lead_time_days']}일 > 30일 — 지금 발주해도 기간 내 입고 불가)"
                st.warning(warn)
            elif r["shortfall_90d"] > 0 and not r["coverable_90d"]:
                st.warning(
                    f"⚠️ 90일 내 약 {math.ceil(r['shortfall_90d'])}개 부족 예상 "
                    f"(리드타임 {r['lead_time_days']}일 > 90일 — 지금 발주해도 기간 내 입고 불가)"
                )
            elif r["shortfall_90d"] > 0:
                st.caption(
                    f"🔵 정상 재주문 신호 — 90일 내 약 {math.ceil(r['shortfall_90d'])}개 소요 예상, "
                    f"리드타임 {r['lead_time_days']}일이면 지금 발주 안 해도 기간 내 조달 가능 (위험 아님)"
                )
            else:
                st.caption("재고 위험 없음")

@st.cache_data(ttl=10)
def _fetch_audit_log(event_type=None, thread_id=None):
    params = {"limit": 200}
    if event_type:
        params["event_type"] = event_type
    if thread_id:
        params["thread_id"] = thread_id
    res = requests.get(f"{BACKEND_URL}/audit_log", params=params, timeout=30)
    res.raise_for_status()
    return res.json()["events"]


# 2026-09-29 CP-M2 교차 검토 지적: 드롭다운이 event_type 원값(영문)을 그대로 보여줬다 -
# 한글 라벨로 표시하고 API에는 원값을 그대로 넘긴다.
EVENT_TYPE_LABELS = {
    "llm_call": "LLM 호출",
    "tool_call": "도구 호출",
    "approval": "승인",
    "rejection": "반려",
    "external_push": "외부 전송(Slack/CMMS)",
    "blocked_by_offline": "오프라인 차단",
}

with tab5:
    st.subheader("🧾 감사 로그")
    # 시각 필드(ts)가 어느 시계 기준인지 표시가 없었다는 지적 - 백엔드가
    # datetime.now().isoformat()으로 기록하므로 타임존 정보 없는 서버 로컬 시간이다.
    st.caption("시각은 백엔드 서버의 로컬 시간(타임존 정보 없음) 기준입니다.")
    col1, col2 = st.columns(2)
    with col1:
        event_type_label = st.selectbox("이벤트 유형", ["전체"] + list(EVENT_TYPE_LABELS.values()))
        label_to_value = {v: k for k, v in EVENT_TYPE_LABELS.items()}
        event_type_filter = label_to_value.get(event_type_label)
    with col2:
        # 방금 승인한 건을 스레드ID로 바로 찾을 방법이 없었다는 지적 - 텍스트 입력으로 필터.
        # 2026-09-29 interface-reviewer 지적(E1): placeholder가 테스트 fixture 형식
        # ("verify-thread-1")이라 실제 UUID와 안 맞아 헷갈렸고, 붙여넣기로 공백이
        # 섞이면 정확히 일치해야 하는 필터가 조용히 0건을 반환했다. 이제 승인 화면에
        # 스레드ID가 실제로 표시되므로(위 pending_approval 카드), 그걸 복사해온다는
        # 걸 명시하고 공백은 트림한다.
        thread_id_filter = st.text_input(
            "스레드 ID로 필터 (선택)", placeholder="승인 화면에 표시된 스레드 ID를 붙여넣으세요"
        ).strip()

    try:
        rows = _fetch_audit_log(event_type_filter, thread_id_filter or None)
    except requests.exceptions.RequestException as e:
        st.error(f"백엔드 요청 실패: {_extract_error_message(e)}")
        rows = None

    if rows is not None:
        if not rows:
            st.caption("조건에 맞는 감사 로그가 없습니다.")
        else:
            display_rows = [{**r, "event_type": EVENT_TYPE_LABELS.get(r["event_type"], r["event_type"])} for r in rows]
            st.dataframe(display_rows, use_container_width=True)

