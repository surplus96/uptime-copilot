import streamlit as st
import requests
import uuid
import re

st.set_page_config(page_title="Uptime Copilot", page_icon="🛡️")
st.title("Uptime Copilot")

BACKEND_URL = "http://localhost:8000"


def _extract_error_message(exc: requests.exceptions.RequestException) -> str:
    """main.py의 구조화된 에러 응답(harness_rejected 등)이 있으면 그 사유를 보여주고,
    없으면 원본 예외 문자열을 그대로 보여준다."""
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            body = response.json()
            if "reason" in body:
                return f"{body.get('error', '오류')}: {body['reason']}"
            if "detail" in body:
                return f"{body.get('error', '오류')}: {body['detail']}"
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


mode = st.sidebar.radio("모드 선택", ["설비 에이전트", "매뉴얼 검색", "이상감지 이벤트"])

# ---------- 설비 에이전트 모드 ----------
if mode == "설비 에이전트":
    if "agent_thread_id" not in st.session_state:
        st.session_state.agent_thread_id = str(uuid.uuid4())
    if "agent_messages" not in st.session_state:
        st.session_state.agent_messages = []
    if "pending_approval" not in st.session_state:
        st.session_state.pending_approval = None

    if st.sidebar.button("새 진단 시작"):
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
        with st.chat_message(message["role"]):
            if message["role"] == "assistant" and message.get("work_order"):
                render_work_order(message["work_order"])
                with st.expander("원본 텍스트 보기"):
                    st.text(message["content"])
            else:
                st.markdown(message["content"])

    if st.session_state.pending_approval:
        with st.container(border=True):
            st.warning("⚠️ 승인이 필요합니다 — 아래 작업지시서를 검토하세요")
            wo = st.session_state.pending_approval.get("work_order")
            if wo:
                render_work_order(wo)
            else:
                st.markdown(st.session_state.pending_approval["message"])
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
                    data = {"result": f"[오류] {_extract_error_message(e)}", "work_order": None}
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

        if prompt:
            thread_id = str(uuid.uuid4())  # 질문마다 새 스레드 - 이전 진단 상태가 새 질문에 남지 않게
            st.session_state.agent_thread_id = thread_id  # 승인 대기 시 재사용하기 위해 저장

            st.session_state.agent_messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"):
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
                st.session_state.pending_approval = {"message": data["message"], "work_order": data.get("work_order")}
            else:
                st.session_state.agent_messages.append(
                    {"role": "assistant", "content": data["result"], "work_order": data.get("work_order")}
                )
            st.rerun()


# ---------- 매뉴얼 검색 모드 ----------
elif mode == "매뉴얼 검색":
    st.subheader("📄 정비 매뉴얼 검색")
    st.caption("예: \"comp3 부품은 어떤 증상과 관련있어?\", \"error5는 무슨 뜻이야?\"")

    query = st.text_input("궁금한 내용을 입력하세요")
    if st.button("검색") and query.strip():
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
else:
    st.subheader("🔔 감지된 이상 이벤트")
    st.caption("전체 설비를 스캔해서 긴급/주의로 판정된 설비 목록입니다. 확인이 필요하면 '설비 에이전트' 탭에서 직접 조회하세요.")

    if st.button("지금 전체 스캔하기"):
        with st.spinner("100대 설비 스캔 중..."):
            try:
                res = requests.post(f"{BACKEND_URL}/scan", timeout=120)
                res.raise_for_status()
            except requests.exceptions.RequestException as e:
                st.error(f"백엔드 요청 실패: {_extract_error_message(e)}")
                st.stop()
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
        st.info("감지된 이벤트가 없습니다.")
    else:
        col_all1, col_all2 = st.columns(2)
        select_all = col_all1.button("전체 선택")
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
                col2.markdown(f"{badge} **설비 #{ev['machine_id']}** · {ev['severity']} · 감지: {ev['detected_at']}")
                col2.caption(ev["diagnosis"])

        selected = [ev["machine_id"] for ev in events if st.session_state.get(f"chk_{ev['machine_id']}", False)]

        st.divider()
        col_a, col_b = st.columns(2)
        if col_a.button(f"처리완료 처리 ({len(selected)}건)", disabled=not selected):
            try:
                requests.post(f"{BACKEND_URL}/events/complete", json={"machine_ids": selected}, timeout=30)
            except requests.exceptions.RequestException as e:
                st.error(f"처리 실패: {_extract_error_message(e)}")
                st.stop()
            st.rerun()
        if col_b.button(f"선택 삭제 ({len(selected)}건)", disabled=not selected):
            try:
                requests.post(f"{BACKEND_URL}/events/delete", json={"machine_ids": selected}, timeout=30)
            except requests.exceptions.RequestException as e:
                st.error(f"삭제 실패: {_extract_error_message(e)}")
                st.stop()
            st.rerun()
