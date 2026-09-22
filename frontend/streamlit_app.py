import streamlit as st
import requests
import uuid
import re
import os


CHAT_AVATARS = {"user": "🧑‍🔧", "assistant": "🛡️"}

st.set_page_config(page_title="Uptime Copilot", page_icon="🛡️", layout="wide")
st.title("Uptime Copilot")

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")


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
                except requests.exceptions.RequestException as e:
                    st.error(f"백엔드 요청 실패: {_extract_error_message(e)}")
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
                # session_state에 저장해서 다른 위젯 클릭으로 재실행돼도 결과가 안 사라지게
                # 한다 - search_clicked는 버튼 누른 바로 다음 실행에서만 True라서, 이 값에
                # 의존해 렌더링하면 그 즉시 다음 재실행에 결과가 사라졌었다
                # (code-quality-reviewer 지적, 2026-09-18).
                st.session_state.rag_result = res.json()
            except requests.exceptions.RequestException as e:
                st.error(f"백엔드 요청 실패: {_extract_error_message(e)}")

    rag_result = st.session_state.get("rag_result")
    if rag_result:
        st.markdown(f"**답변**\n\n{rag_result['answer']}")
        if rag_result.get("context"):
            with st.expander("📄 참고한 매뉴얼 원문 보기"):
                st.text(rag_result["context"])

@st.fragment(run_every="10s")
def _simulator_panel():
    try:
        res = requests.get(f"{BACKEND_URL}/simulator/status", timeout=10)
        res.raise_for_status()
        status = res.json()
    except requests.exceptions.RequestException as e:
        st.warning(f"시뮬레이터 상태 조회 실패: {_extract_error_message(e)}")
        return

    with st.container(border=True):
        col1, col2, col3 = st.columns([2, 2, 1])
        running = status["running"]
        col1.markdown(f"{'🟢 실행 중' if running else '⏸️ 정지'} · 시뮬레이션 시각: `{status['sim_now'] or '없음'}`")

        speed = f"속도: 실제 {status['tick_seconds']}초 = 시뮬레이션 {status['hours_per_tick']}시간"
        if running and status["elapsed_seconds"] is not None:
            m, s = divmod(status["elapsed_seconds"], 60)
            speed += f" · 실행 경과 {m}분 {s}초"
        col2.caption(speed)

        if running:
            if col3.button("정지", key="sim_stop"):
                requests.post(f"{BACKEND_URL}/simulator/stop", timeout=10)
                st.rerun()
        else:
            if col3.button("시작", key="sim_start"):
                requests.post(f"{BACKEND_URL}/simulator/start", timeout=10)
                st.rerun()

        if running and status["seconds_since_last_tick"] is not None:
            remaining = max(0, status["tick_seconds"] - status["seconds_since_last_tick"])
            st.caption(f"⏳ 다음 틱까지 약 {remaining}초 (10초마다 이 화면 자동 갱신)")

        if status["has_stale_events"]:
            st.warning("설비는 전부 정상인데 감지 목록에 이전 데이터가 남아있습니다. 시뮬레이터를 새로 시작하기 전에 리셋을 권장합니다.")

        col_r1, col_r2 = st.columns([3, 1])
        col_r1.caption("리셋: 시뮬레이션 상태·데이터와 감지 목록을 전부 초기화합니다(자동으로는 지워지지 않음).")
        if col_r2.button("리셋", key="sim_reset"):
            requests.post(f"{BACKEND_URL}/simulator/reset", timeout=10)
            st.session_state.pop("sim_event_count", None)
            st.rerun()

        degrading = status["degrading"]
        if degrading:
            st.caption(f"열화 진행 중인 설비 {len(degrading)}대")
            for d in sorted(degrading, key=lambda x: -x["progress"]):
                label = f"설비 #{d['machine_id']} · {d['signal']} · {d['state']} · 오류 {d['errors_emitted']}건"
                st.progress(min(d["progress"], 1.0), text=label)
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
