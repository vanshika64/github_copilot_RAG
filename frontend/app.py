"""
Streamlit frontend for GitHub RAG Copilot.

Run with:
    streamlit run app.py
(from inside frontend/, with the backend running on BACKEND_URL, default
http://localhost:8000)
"""
import os
import time

import requests
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

st.set_page_config(page_title="GitHub RAG Copilot", page_icon="🧠", layout="wide")

# ----------------------------- Session state ----------------------------------
defaults = {
    "repo_id": None,
    "repo_url": "",
    "indexed": False,
    "stats": None,
    "chat_history": [],
    "last_sources": [],
    "suggested_questions": [],
    "pending_question": None,
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v


# ----------------------------- API helpers -------------------------------------
def api_post(path, json_body=None):
    try:
        return requests.post(f"{BACKEND_URL}{path}", json=json_body, timeout=120)
    except requests.RequestException as e:
        return _FakeErrorResponse(str(e))


def api_get(path, params=None):
    try:
        return requests.get(f"{BACKEND_URL}{path}", params=params, timeout=120)
    except requests.RequestException as e:
        return _FakeErrorResponse(str(e))


class _FakeErrorResponse:
    """Stand-in for a requests.Response when the request itself fails
    (backend down, connection refused, timeout) so callers can treat it
    uniformly instead of crashing on a missing .json()/.status_code."""
    def __init__(self, message):
        self.status_code = 0
        self.text = message

    def json(self):
        return {"detail": self.text}


def safe_detail(resp) -> str:
    """
    Best-effort extraction of an error message from a response, without
    ever raising. The backend usually returns JSON like {"detail": "..."},
    but on a raw 500, a proxy error page, an empty body, or a dropped
    connection the body may not be valid JSON at all -- resp.json() would
    raise JSONDecodeError in that case, so we guard it here.
    """
    try:
        data = resp.json()
        if isinstance(data, dict) and "detail" in data:
            return str(data["detail"])
        return str(data)
    except Exception:
        text = getattr(resp, "text", "") or ""
        return text.strip() or f"Request failed with status {getattr(resp, 'status_code', '?')}"


# ----------------------------- Sidebar nav --------------------------------------
st.sidebar.title("🧠 GitHub RAG Copilot")
page = st.sidebar.radio(
    "Navigate",
    ["1️⃣ Repository Setup", "2️⃣ Repository Chat", "3️⃣ Repository Explorer"],
)

if st.session_state.repo_id:
    st.sidebar.success(f"Active repo: `{st.session_state.repo_id}`")
else:
    st.sidebar.info("No repository indexed yet.")


# ============================================================================
# PAGE 1 — Repository Setup
# ============================================================================
if page.startswith("1"):
    st.title("1️⃣ Repository Setup")
    st.caption("Enter a public (or token-accessible private) GitHub repository to index.")

    with st.form("repo_setup_form"):
        url = st.text_input("GitHub repository URL", placeholder="https://github.com/owner/repo")
        token = st.text_input("GitHub token (optional, for private repos)", type="password")
        submitted = st.form_submit_button("Validate & Index Repository", use_container_width=True)

    if submitted:
        if not url.strip():
            st.error("Please enter a GitHub URL.")
        else:
            with st.spinner("Validating repository..."):
                resp = api_post("/api/repository/validate", {"url": url, "github_token": token or None})
                if resp.status_code != 200:
                    result = {"valid": False, "message": safe_detail(resp)}
                else:
                    try:
                        result = resp.json()
                    except Exception:
                        result = {"valid": False, "message": "Backend returned an unreadable response."}

            if not result.get("valid"):
                st.error(f"❌ {result.get('message')}")
            else:
                st.success(f"✅ {result.get('message')}")
                col1, col2, col3 = st.columns(3)
                col1.metric("⭐ Stars", result.get("stars", "—"))
                col2.metric("Language", result.get("language") or "—")
                col3.metric("Private", "Yes" if result.get("is_private") else "No")
                if result.get("description"):
                    st.caption(result["description"])

                # kick off indexing
                idx_resp = api_post("/api/repository/index", {"url": url, "github_token": token or None})
                if idx_resp.status_code != 200:
                    st.error(f"Failed to start indexing: {safe_detail(idx_resp)}")
                else:
                    try:
                        repo_id = idx_resp.json()["repo_id"]
                    except Exception:
                        st.error("Backend returned an unreadable response when starting indexing.")
                        st.stop()
                    st.session_state.repo_id = repo_id
                    st.session_state.repo_url = url
                    st.session_state.indexed = False

                    progress_bar = st.progress(0, text="Starting...")
                    status_box = st.empty()
                    consecutive_failures = 0
                    while True:
                        status_resp = api_get(f"/api/repository/status/{repo_id}")
                        if status_resp.status_code != 200:
                            consecutive_failures += 1
                            status_box.warning(f"⚠️ Could not fetch status ({safe_detail(status_resp)}). Retrying...")
                            if consecutive_failures >= 10:
                                status_box.error("❌ Lost contact with the backend while indexing. "
                                                  "Check the backend terminal (it may have restarted).")
                                break
                            time.sleep(1.2)
                            continue
                        consecutive_failures = 0
                        try:
                            status = status_resp.json()
                        except Exception:
                            status_box.warning("⚠️ Backend returned an unreadable status. Retrying...")
                            time.sleep(1.2)
                            continue
                        pct = status.get("progress", 0)
                        progress_bar.progress(pct / 100, text=f"{status.get('stage')}: {status.get('message')}")
                        if status.get("done"):
                            if status.get("error"):
                                status_box.error(f"❌ Indexing failed: {status['error']}")
                            else:
                                status_box.success("✅ Indexing complete! Head to Repository Chat or Explorer.")
                                st.session_state.indexed = True
                            break
                        time.sleep(1.2)

    if st.session_state.indexed and st.session_state.repo_id:
        st.divider()
        st.subheader("📊 Repository Statistics")
        stats_resp = api_get(f"/api/repository/stats/{st.session_state.repo_id}")
        if stats_resp.status_code == 200:
            stats = stats_resp.json()
            st.session_state.stats = stats
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Files indexed", stats["total_files_indexed"])
            c2.metric("Chunks created", stats["total_chunks"])
            c3.metric("Total lines", stats["total_lines"])
            c4.metric("⭐ Stars", stats.get("stars") or "—")
            if stats.get("languages"):
                st.bar_chart(stats["languages"])

        st.subheader("🧭 Repository Summary")
        sum_resp = api_get(f"/api/repository/summary/{st.session_state.repo_id}")
        if sum_resp.status_code == 200:
            summary = sum_resp.json()
            st.write(summary["summary"])
            colA, colB = st.columns(2)
            with colA:
                st.markdown("**Key features**")
                for f in summary.get("key_features", []):
                    st.markdown(f"- {f}")
            with colB:
                st.markdown("**Tech stack**")
                for t in summary.get("tech_stack", []):
                    st.markdown(f"- {t}")


# ============================================================================
# PAGE 2 — Repository Chat
# ============================================================================
elif page.startswith("2"):
    st.title("2️⃣ Repository Chat")

    if not st.session_state.repo_id or not st.session_state.indexed:
        st.warning("Please index a repository first on the **Repository Setup** page.")
    else:
        st.caption(f"Chatting with `{st.session_state.repo_id}`")

        # Suggested questions
        if not st.session_state.suggested_questions:
            sq_resp = api_get(f"/api/repository/suggested-questions/{st.session_state.repo_id}")
            if sq_resp.status_code == 200:
                st.session_state.suggested_questions = sq_resp.json().get("questions", [])

        if st.session_state.suggested_questions:
            st.markdown("**💡 Suggested questions**")
            cols = st.columns(3)
            for i, q in enumerate(st.session_state.suggested_questions[:6]):
                if cols[i % 3].button(q, key=f"suggest_{i}", use_container_width=True):
                    st.session_state.pending_question = q

        # Render chat history
        for msg in st.session_state.chat_history:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])
                if msg["role"] == "assistant" and msg.get("sources"):
                    with st.expander("📚 Sources"):
                        for s in msg["sources"]:
                            st.markdown(
                                f"**`{s['file_path']}`** — {s['chunk_type']} `{s['name']}` "
                                f"(lines {s['start_line']}-{s['end_line']}, score={s['score']:.3f})"
                            )
                            st.code(s["snippet"], language="python")

        typed = st.chat_input("Ask something about this repository...")
        query = st.session_state.pending_question or typed
        st.session_state.pending_question = None

        if query:
            st.session_state.chat_history.append({"role": "user", "content": query})
            with st.chat_message("user"):
                st.markdown(query)

            with st.chat_message("assistant"):
                with st.spinner("Retrieving relevant code & generating answer..."):
                    history_payload = [
                        {"role": m["role"], "content": m["content"]}
                        for m in st.session_state.chat_history[:-1]
                    ]
                    resp = api_post(
                        "/api/chat/query",
                        {"repo_id": st.session_state.repo_id, "query": query, "history": history_payload},
                    )
                    if resp.status_code != 200:
                        answer = f"❌ Error: {safe_detail(resp)}"
                        sources = []
                    else:
                        try:
                            data = resp.json()
                            answer = data["answer"]
                            sources = data["sources"]
                        except Exception:
                            answer = "❌ Error: backend returned an unreadable response."
                            sources = []

                st.markdown(answer)
                if sources:
                    with st.expander("📚 Sources"):
                        for s in sources:
                            st.markdown(
                                f"**`{s['file_path']}`** — {s['chunk_type']} `{s['name']}` "
                                f"(lines {s['start_line']}-{s['end_line']}, score={s['score']:.3f})"
                            )
                            st.code(s["snippet"], language="python")

            st.session_state.chat_history.append(
                {"role": "assistant", "content": answer, "sources": sources}
            )
            st.rerun()

        if st.session_state.chat_history:
            if st.button("🗑️ Clear chat"):
                st.session_state.chat_history = []
                st.rerun()


# ============================================================================
# PAGE 3 — Repository Explorer
# ============================================================================
else:
    st.title("3️⃣ Repository Explorer")

    if not st.session_state.repo_id or not st.session_state.indexed:
        st.warning("Please index a repository first on the **Repository Setup** page.")
    else:
        tab1, tab2, tab3 = st.tabs(["📁 Browse Files", "🔍 Explain File", "🏗️ Architecture Overview"])

        with tab1:
            tree_resp = api_get(f"/api/repository/files/{st.session_state.repo_id}")
            if tree_resp.status_code == 200:
                tree = tree_resp.json()["tree"]

                def render_tree(node, prefix=""):
                    for key, val in sorted(node.items()):
                        if key == "__files__":
                            for fname in sorted(val):
                                full = f"{prefix}{fname}"
                                if st.button(f"📄 {full}", key=f"file_{full}"):
                                    st.session_state["selected_file"] = full
                        else:
                            with st.expander(f"📂 {key}", expanded=False):
                                render_tree(val, prefix=f"{prefix}{key}/")

                render_tree(tree)

                if st.session_state.get("selected_file"):
                    fpath = st.session_state["selected_file"]
                    content_resp = api_get(
                        f"/api/repository/file-content/{st.session_state.repo_id}", params={"path": fpath}
                    )
                    if content_resp.status_code == 200:
                        data = content_resp.json()
                        st.subheader(f"`{fpath}`")
                        st.code(data["content"], language=data["language"])

        with tab2:
            fpath = st.text_input("File path to explain", value=st.session_state.get("selected_file", ""))
            if st.button("Explain this file"):
                with st.spinner("Analyzing file..."):
                    resp = api_post(
                        "/api/repository/explain-file",
                        {"repo_id": st.session_state.repo_id, "path": fpath},
                    )
                if resp.status_code == 200:
                    st.markdown(resp.json()["explanation"])
                else:
                    st.error(safe_detail(resp))

        with tab3:
            if st.button("Generate / Refresh architecture overview"):
                pass
            arch_resp = api_get(f"/api/repository/architecture/{st.session_state.repo_id}")
            if arch_resp.status_code == 200:
                st.markdown(arch_resp.json()["overview"])
            else:
                st.info("Architecture overview not available yet.")
