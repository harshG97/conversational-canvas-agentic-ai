"""Streamlit chat UI for the Canvas LMS AI agent."""

from __future__ import annotations

import os

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from canvas_agent import setup_logging
setup_logging()

# ── Page config ──────────────────────────────────────────────────────

st.set_page_config(page_title="Canvas LMS AI Agent", page_icon="📚")
st.title("Canvas LMS AI Agent")
st.caption("Ask about your upcoming assignments, quizzes, and deadlines.")

# ── Handle Google OAuth callback ────────────────────────────────────
# Google redirects back with ?code=...&state=... after user authorises.

if "code" in st.query_params and "google_creds" not in st.session_state:
    from canvas_agent.google_calendar import exchange_code

    _redirect_uri = os.environ.get("APP_URL", "http://localhost:8501").rstrip("/")
    try:
        _creds = exchange_code(st.query_params["code"], _redirect_uri)
        st.session_state.google_creds = _creds
        st.query_params.clear()
        st.rerun()
    except Exception as _exc:
        st.error(f"Google Calendar connection failed: {_exc}")
        st.query_params.clear()

# ── Validate Canvas env vars ─────────────────────────────────────────

canvas_missing = [
    v
    for v in ("CANVAS_API_URL", "CANVAS_API_TOKEN")
    if not os.environ.get(v)
]
if canvas_missing:
    st.error(
        f"Missing environment variables: **{', '.join(canvas_missing)}**. "
        "Copy `.env.example` to `.env` and fill in your values."
    )
    st.stop()

# ── Lazy-import agent (after env validation) ─────────────────────────

from canvas_agent.agent import DEFAULT_MODEL, Agent  # noqa: E402

# ── Sidebar: model picker ────────────────────────────────────────────

AVAILABLE_MODELS = {
    "Claude Sonnet 4.5": "claude-sonnet-4-5-20250929",
    "Claude Haiku 4.5": "claude-haiku-4-5-20251001",
    #"Claude Opus 4.6": "claude-opus-4-6",
    "Llama 4 Scout 17B (Groq)": "meta-llama/llama-4-scout-17b-16e-instruct",
    "Llama 3.3 70B (Groq)": "llama-3.3-70b-versatile",
    "Llama 3.1 8B (Groq)": "llama-3.1-8b-instant",
    #"Mixtral 8x7B (Groq)": "mixtral-8x7b-32768",
}

# Resolve which model name to pre-select based on env / default
_env_model = os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL)
_default_label = next(
    (k for k, v in AVAILABLE_MODELS.items() if v == _env_model),
    "Claude Sonnet 4.5",
)

with st.sidebar:
    st.header("Settings")
    selected_label = st.selectbox(
        "Model",
        list(AVAILABLE_MODELS.keys()),
        index=list(AVAILABLE_MODELS.keys()).index(_default_label),
    )
    selected_model = AVAILABLE_MODELS[selected_label]

    # Check that the right API key is set for the selected provider
    is_groq = "Groq" in selected_label
    if is_groq and not os.environ.get("GROQ_API_KEY"):
        st.warning("Set **GROQ_API_KEY** in `.env` to use Groq models.")
    elif not is_groq and not os.environ.get("ANTHROPIC_API_KEY"):
        st.warning("Set **ANTHROPIC_API_KEY** in `.env` to use Claude models.")
    
    if st.button("Clear conversation"):
        st.session_state.messages = []
        st.session_state.pop("agent", None)
        st.rerun()

    st.divider()

    # ── Canvas account ──────────────────────────────────────────────
    st.subheader("Canvas Account")
    st.markdown(
        "By default, connected to **Harsh Gupta's** Canvas account. "
        "To connect your own, paste your Canvas API token below."
    )

    custom_token = st.text_input(
        "Canvas API Token",
        type="password",
        placeholder="Leave blank to use default account",
    )

    with st.expander("How to generate a Canvas API token"):
        st.markdown(
            "1. Log in to Canvas and go to **Account → Settings**.\n"
            "2. Scroll to **Approved Integrations**.\n"
            "3. Click **+ New Access Token**.\n"
            "4. Enter a purpose (e.g. *AI Agent*), click **Generate Token**.\n"
            "5. Copy the token and paste it above."
        )

    # Resolve the effective token (custom or None for env default)
    canvas_token = custom_token.strip() if custom_token and custom_token.strip() else None

    #st.divider()

    # ── Google Calendar ─────────────────────────────────────────────
    st.subheader("Google Calendar")

    _has_client_env = bool(
        os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET")
    )

    if "google_creds" in st.session_state:
        st.success("Google Calendar connected!")
        if st.button("Disconnect Google Calendar"):
            del st.session_state["google_creds"]
            st.rerun()
    elif _has_client_env:
        _redirect_uri = os.environ.get("APP_URL", "http://localhost:8501").rstrip("/")
        from canvas_agent.google_calendar import get_auth_url

        _auth_url, _state = get_auth_url(_redirect_uri)

        # Streamlit Cloud's iframe sandbox blocks same-tab navigation,
        # and Google refuses to load inside iframes. A new tab is the
        # only reliable path — st.link_button handles this natively.
        st.link_button("Connect Google Calendar", _auth_url, type="primary")
        st.caption(
            "Opens a new tab for Google sign-in. "
            "After authorizing, continue using that tab."
        )
    else:
        st.caption(
            "Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` "
            "in your environment to enable Google Calendar."
        )

    st.divider()


# ── Session state ────────────────────────────────────────────────────

if "messages" not in st.session_state:
    st.session_state.messages = []

# Resolve google credentials (session-based web OAuth, or None for local fallback)
google_creds = st.session_state.get("google_creds")

# Recreate the agent if the model, canvas token, or google creds changed
_needs_new_agent = (
    "agent" not in st.session_state
    or st.session_state.get("current_model") != selected_model
    or st.session_state.get("current_canvas_token") != canvas_token
    or st.session_state.get("current_google_creds") is not google_creds
)
if _needs_new_agent:
    # Clear chat history when the Canvas account changes so stale data
    # from the previous account doesn't linger in the conversation.
    if st.session_state.get("current_canvas_token") != canvas_token:
        st.session_state.messages = []
    st.session_state.agent = Agent(
        model=selected_model,
        canvas_token=canvas_token,
        google_creds=google_creds,
    )
    st.session_state.current_model = selected_model
    st.session_state.current_canvas_token = canvas_token
    st.session_state.current_google_creds = google_creds

# ── Render chat history ──────────────────────────────────────────────

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# ── Handle new user input ───────────────────────────────────────────

if prompt := st.chat_input("Ask about your Canvas deadlines..."):
    # Show user message
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Show assistant response with status updates
    with st.chat_message("assistant"):
        status_container = st.status("Thinking...", expanded=True)
        text_container = st.empty()

        # Accumulate streamed text
        streamed_parts: list[str] = []

        def _on_tool_start(label: str) -> None:
            # This is called from the main thread (before tool dispatch)
            status_container.update(label=label, state="running")

        def _on_text_chunk(text: str) -> None:
            streamed_parts.append(text)
            text_container.markdown("".join(streamed_parts))

        # Patch callbacks into the existing agent for this turn.
        # on_tool_detail is left as None — it fires from worker threads
        # where Streamlit widgets can't be safely updated.
        agent: Agent = st.session_state.agent
        agent._on_text_chunk = _on_text_chunk
        agent._handler._on_tool_start = _on_tool_start
        agent._handler._on_tool_detail = None

        reply = agent.chat(prompt)

        # Collapse the status expander when done
        status_container.update(label="Done", state="complete", expanded=False)

        # Make sure final text is rendered
        if not streamed_parts:
            text_container.markdown(reply)

    st.session_state.messages.append({"role": "assistant", "content": reply})
