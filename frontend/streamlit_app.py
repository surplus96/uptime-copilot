import streamlit as st
import requests

st.title("AI Chatbot (OpenAI backend)")

BACKEND_URL = "http://localhost:8000"  # FastAPI 서버 주소

# 백엔드 세션 ID 초기화 (없으면 새로 발급받음)
if "session_id" not in st.session_state:
    try:
        res = requests.post(f"{BACKEND_URL}/session")
        res.raise_for_status()
        st.session_state.session_id = res.json()["session_id"]
    except requests.exceptions.RequestException as e:
        st.error(f"백엔드 서버에 연결할 수 없습니다: {e}")
        st.stop()

# 화면 표시용 메시지 히스토리 초기화
if "messages" not in st.session_state:
    st.session_state.messages = []

# 기존 대화 내역 표시
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# 사용자 입력 처리
if prompt := st.chat_input("What is up?"):
    # 사용자 메시지 저장 및 표시
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # 어시스턴트 응답 (백엔드 스트리밍 엔드포인트 호출)
    with st.chat_message("assistant"):
        def stream_response():
            try:
                with requests.post(
                    f"{BACKEND_URL}/chat/stream",
                    json={
                        "message": prompt,
                        "session_id": st.session_state.session_id,
                    },
                    stream=True,
                    timeout=60,
                ) as res:
                    res.raise_for_status()
                    for chunk in res.iter_content(chunk_size=None, decode_unicode=True):
                        if chunk:
                            yield chunk
            except requests.exceptions.RequestException as e:
                yield f"\n[오류] 백엔드 요청 실패: {e}"

        response = st.write_stream(stream_response())

    st.session_state.messages.append({"role": "assistant", "content": response})
