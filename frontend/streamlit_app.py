import streamlit as st
import requests
import uuid
import re

CHAT_AVATARS = {"user": "🧑‍🔧", "assistant": "🛡️"}

st.set_page_config(page_title="Uptime Copilot", page_icon="🛡️", layout="wide")
st.title("Uptime Copilot")

BACKEND_URL = "http://localhost:8000"


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
        r"\[부품\](.*?)\[증상\](.*?)\[매뉴얼 근거\](.*?)\[조치사항\](.*?)\[긴급도\](.*?)(?=\[부품\]|$)",
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
        })
    return blocks


def render_work_order(work_order_text: str):
    """부품별로 파싱해서 카드로 보여준다. 파싱 실패 시 원본 텍스트를 그대로 보여주는 폴백."""
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


tab1, tab2, tab3 = st.tabs(["설비 에이전트", "매뉴얼 검색", "이상감지 이벤트"])

# ---------- 설비 에이전트 모드 ----------
with tab1:
    if "agent_thread_id" not in st.session_state:
        st.session_state.agent_thread_id = str(uuid.uuid4())
    if "agent_messages" not in st.session_state:
        st.session_state.agent_messages = []
    if "pending_approval" not in st.session_state:
        st.session_state.pending_approval = None

    reset_confirmed = True
    if st.session_state.pending_approval:
        reset_confirmed = st.sidebar.checkbox(
            "⚠️ 승인 대기 중인 항목이 있습니다 - 포기하고 새로 시작하기",
        )
    if st.sidebar.button("새 진단 시작", disabled=not reset_confirmed):
        st.session_state.agent_thread_id = str(uuid.uuid4())
        st.session_state.agent_messages = []
        st.session_state.pending_approval = None
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
                    st.success("✅ 승인됨 — Slack 알림과 CMMS 작업지시서 등록을 서버에서 자동 처리했습니다.")
                    st.caption("전송 성공 여부는 이 화면에 표시되지 않습니다(외부 시스템 장애가 승인 자체를 "
                               "막지 않도록 한 설계) — 확인이 필요하면 Slack 채널이나 CMMS에서 직접 보세요.")
                elif content.startswith("[긴급 반려됨]"):
                    st.warning("🚫 반려됨 — 별도 조치는 이루어지지 않았습니다.")
                render_work_order(message["work_order"])
                with st.expander("처리 결과 상세"):
                    st.text(content)
            else:
                st.markdown(message["content"])

    if st.session_state.pending_approval:
        with st.container(border=True):
            st.warning("⚠️ 승인이 필요합니다 — 아래 작업지시서를 검토하세요")
            st.caption("승인하면 Slack 긴급 채널로 알림이 발송되고 CMMS에 긴급(HIGH) 작업지시서가 "
                       "등록됩니다. 반려하면 아무 것도 전송되지 않습니다.")
            wo = st.session_state.pending_approval.get("work_order")
            if wo:
                render_work_order(wo)
            else:
                st.markdown(st.session_state.pending_approval["message"])

            PERSPECTIVE_ORDER = ["안전", "생산", "정비"]
            ICONS = {"안전": "🛡️", "생산": "🏭", "정비": "🛠️"}

            perspectives = st.session_state.pending_approval.get("perspectives") or []
            parsed = {}
            for p in perspectives:
                label, sep, text = p.partition("] ")
                label = label.lstrip("[") if sep else ""
                parsed.setdefault(label, (text or p).strip())

            if parsed:
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

            col1, col2 = st.columns(2)

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
                    st.error(
                        f"승인 결과를 확인하지 못했습니다: {_extract_error_message(e)}\n\n"
                        "요청이 서버에 전달되어 이미 처리됐을 수도 있습니다. 다시 누르기 전에 "
                        "Slack 채널이나 CMMS에서 작업지시서가 이미 등록됐는지 확인하세요."
                    )
                    return
                st.session_state.agent_messages.append(
                    {"role": "assistant", "content": data["result"], "work_order": data.get("work_order")}
                )
                st.session_state.pending_approval = None
                st.rerun()

            if col1.button("승인", use_container_width=True):
                _resume(True)
            if col2.button("반려", use_container_width=True):
                _resume(False)
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
                try:
                    res = requests.post(
                        f"{BACKEND_URL}/agent/query",
                        json={"message": prompt, "thread_id": thread_id},
                        timeout=60,
                    )
                    res.raise_for_status()
                    data = res.json()
                except requests.exceptions.RequestException as e:
                    st.error(f"백엔드 요청 실패: {_extract_error_message(e)}")
                    st.stop()

            if data["status"] == "pending_approval":
                st.session_state.pending_approval = {
                    "message": data["message"],
                    "work_order": data.get("work_order"),
                    "perspectives": data.get("perspectives", []),   # ← 추가
                }

            else:
                st.session_state.agent_messages.append(
                    {"role": "assistant", "content": data["result"], "work_order": data.get("work_order")}
                )
            st.rerun()


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
                data = res.json()
            except requests.exceptions.RequestException as e:
                st.error(f"백엔드 요청 실패: {_extract_error_message(e)}")
                st.stop()

        st.markdown(f"**답변**\n\n{data['answer']}")
        if data.get("context"):
            with st.expander("📄 참고한 매뉴얼 원문 보기"):
                st.text(data["context"])


# ---------- 이상감지 이벤트 모드 ----------
with tab3:
    st.subheader("🔔 감지된 이상 이벤트")
    st.caption("전체 설비를 스캔해서 긴급/주의로 판정된 설비 목록입니다. 확인이 필요하면 '설비 에이전트' 탭에서 직접 조회하세요.")
    st.caption("🔴 긴급: 실제 고장 이력 확인됨 · 🟡 주의: 통계적 이상 징후(사전 경보), 고장 확정 아님")
    st.caption(
        "⏱️ 진단 내용의 날짜(예: 2016-01-01)는 오늘 날짜가 아니라 시뮬레이션 데이터셋 자체의 시각입니다 "
        "(Azure PdM 데이터셋 2014~2016년 + `/simulate/tick`으로 전진시킨 시간). "
        "'마지막 스캔'만 실제 스캔 버튼을 누른 오늘 시각입니다."
    )
    
    if "event_feedback" in st.session_state:
        st.success(st.session_state.pop("event_feedback"))

    if st.button("지금 전체 스캔하기"):
        with st.spinner("100대 설비 스캔 중..."):
            try:
                res = requests.post(f"{BACKEND_URL}/scan", timeout=120)
                res.raise_for_status()
                detected_count = res.json()["detected_count"]
            except requests.exceptions.RequestException as e:
                st.error(f"백엔드 요청 실패: {_extract_error_message(e)}")
                st.stop()
        st.session_state.has_scanned = True
        st.session_state.event_feedback = (
            f"스캔 완료: {detected_count}건 발견" if detected_count else "스캔 완료: 이상 없음"
        )
        st.rerun()

    try:
        res = requests.get(f"{BACKEND_URL}/events", params={"limit": 10}, timeout=30)
        res.raise_for_status()
        data = res.json()
        events = data["events"]
        total = data["total"]
    except requests.exceptions.RequestException as e:
        st.error(f"백엔드 요청 실패: {_extract_error_message(e)}")
        st.stop()

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
        clear_all = col_all2.button("전체 해제")

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
            try:
                res = requests.post(f"{BACKEND_URL}/events/complete", json={"machine_ids": selected}, timeout=30)
                res.raise_for_status()
                completed_count = res.json()["completed_count"]
            except requests.exceptions.RequestException as e:
                st.error(f"처리 실패: {_extract_error_message(e)}")
                st.stop()
            st.session_state.event_feedback = f"{completed_count}건 완료 처리했습니다."
            st.rerun()
        if col_b.button(f"선택 삭제 ({len(selected)}건)", disabled=not selected):
            try:
                res = requests.post(f"{BACKEND_URL}/events/delete", json={"machine_ids": selected}, timeout=30)
                res.raise_for_status()
                deleted_count = res.json()["deleted_count"]
            except requests.exceptions.RequestException as e:
                st.error(f"삭제 실패: {_extract_error_message(e)}")
                st.stop()
            st.session_state.event_feedback = f"{deleted_count}건 삭제했습니다."
            st.rerun()
