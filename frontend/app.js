const state = { repoId: null, repoUrl: "", indexed: false, chat: [], treeLoaded: false, questionsLoaded: false };
let apiBase = localStorage.getItem("copilot-api-url") || "https://github-copilot-backend.onrender.com";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

function setApiLabel() {
  try { $("#api-label").textContent = new URL(apiBase).host; } catch { $("#api-label").textContent = apiBase; }
}
function toast(message, error = false) {
  const node = $("#toast"); node.textContent = message; node.classList.toggle("error", error); node.classList.remove("hidden");
  clearTimeout(toast.timer); toast.timer = setTimeout(() => node.classList.add("hidden"), 4500);
}
function detail(payload) {
  return typeof payload === "object" && payload?.detail ? payload.detail : (typeof payload === "string" ? payload : "The request could not be completed.");
}
async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(`${apiBase}${path}`, { headers: { "Content-Type": "application/json", ...(options.headers || {}) }, ...options });
  } catch { throw new Error(`Could not reach the API at ${apiBase}. Start the backend and check API settings.`); }
  let payload;
  try { payload = await response.json(); } catch { payload = null; }
  if (!response.ok) throw new Error(detail(payload) || `Request failed (${response.status}).`);
  return payload;
}
const escapeHtml = (value = "") => String(value).replace(/[&<>'"]/g, char => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", "'":"&#39;", '"':"&quot;" }[char]));
function richText(value = "") {
  return escapeHtml(value).split(/\n{2,}/).map(part => `<p>${part.replace(/\n/g, "<br>").replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")}</p>`).join("");
}
function setRepository(repoId, url) {
  state.repoId = repoId; state.repoUrl = url; state.indexed = true; state.treeLoaded = false; state.questionsLoaded = false;
  $("#side-status").innerHTML = `<span class="status-dot"></span><div><small>ACTIVE REPOSITORY</small><strong title="${escapeHtml(repoId)}">${escapeHtml(repoId)}</strong></div>`;
  $("#breadcrumb").innerHTML = `WORKSPACE / <b>${escapeHtml(repoId).toUpperCase()}</b>`;
  $$('[data-requires-repo]').forEach(node => node.classList.add("hidden"));
  $$('[data-repo-content]').forEach(node => node.classList.remove("hidden"));
}
function showView(name) {
  $$(".view").forEach(view => view.classList.toggle("active", view.id === `${name}-view`));
  $$(".nav-link").forEach(link => link.classList.toggle("active", link.dataset.view === name));
  if (name === "chat" && state.indexed) loadQuestions();
  if (name === "explorer" && state.indexed) loadTree();
  window.scrollTo({ top: 0, behavior: "smooth" });
}
function showExplorerTab(name) {
  $$(".tab").forEach(button => button.classList.toggle("active", button.dataset.tab === name));
  $$(".tab-panel").forEach(panel => panel.classList.toggle("active", panel.id === `${name}-tab`));
  if (name === "files" && state.indexed) loadTree();
  if (name === "architecture" && state.indexed) architecture();
}
function setProgress(status) {
  const value = Math.max(0, Math.min(100, Number(status.progress) || 0));
  $("#progress-panel").classList.remove("hidden"); $("#progress-stage").textContent = String(status.stage || "WORKING").replaceAll("_", " ").toUpperCase();
  $("#progress-message").textContent = status.message || "Working…"; $("#progress-value").textContent = `${value}%`; $("#progress-bar").style.width = `${value}%`;
}
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));

async function indexRepository(event) {
  event.preventDefault();
  const url = $("#repo-url").value.trim(), token = $("#github-token").value.trim(), button = $("#index-button");
  if (!url) return toast("Enter a GitHub repository URL first.", true);
  button.disabled = true; button.querySelector("span").textContent = "Validating repository…"; $("#repository-result").innerHTML = "";
  try {
    const validation = await request("/api/repository/validate", { method: "POST", body: JSON.stringify({ url, github_token: token || null }) });
    if (!validation.valid) throw new Error(validation.message || "That repository could not be validated.");
    renderValidation(validation);
    button.querySelector("span").textContent = "Starting index…";
    const start = await request("/api/repository/index", { method: "POST", body: JSON.stringify({ url, github_token: token || null }) });
    await pollIndex(start.repo_id, url);
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.querySelector("span").textContent = "Index repository"; }
}
function renderValidation(repo) {
  $("#repository-result").innerHTML = `<div class="result-grid"><div class="metric"><small>STARS</small><b>${repo.stars ?? "—"}</b></div><div class="metric"><small>PRIMARY LANGUAGE</small><b>${escapeHtml(repo.language || "—")}</b></div><div class="metric"><small>ACCESS</small><b>${repo.is_private ? "Private" : "Public"}</b></div><div class="metric"><small>DEFAULT BRANCH</small><b>${escapeHtml(repo.default_branch || "—")}</b></div></div>${repo.description ? `<article class="surface summary-panel"><p class="eyebrow">REPOSITORY FOUND</p><h2>${escapeHtml(repo.owner || "")}/${escapeHtml(repo.repo || "")}</h2><p>${escapeHtml(repo.description)}</p></article>` : ""}`;
}
async function pollIndex(repoId, url) {
  let failures = 0;
  while (failures < 8) {
    try {
      const status = await request(`/api/repository/status/${encodeURIComponent(repoId)}`); failures = 0; setProgress(status);
      if (status.done) {
        if (status.error) throw new Error(`Indexing failed: ${status.error}`);
        setRepository(repoId, url); toast("Repository indexed. Your workspace is ready."); await loadRepositoryInsights(); return;
      }
    } catch (error) { failures += 1; if (failures >= 8) throw error; $("#progress-message").textContent = "Reconnecting to the indexing service…"; }
    await wait(1200);
  }
}
async function loadRepositoryInsights() {
  try {
    const [stats, summary] = await Promise.all([request(`/api/repository/stats/${encodeURIComponent(state.repoId)}`), request(`/api/repository/summary/${encodeURIComponent(state.repoId)}`)]);
    $("#repository-result").insertAdjacentHTML("beforeend", `<div class="result-grid"><div class="metric"><small>FILES INDEXED</small><b>${stats.total_files_indexed}</b></div><div class="metric"><small>CHUNKS CREATED</small><b>${stats.total_chunks}</b></div><div class="metric"><small>LINES READ</small><b>${stats.total_lines.toLocaleString()}</b></div><div class="metric"><small>LANGUAGES</small><b>${Object.keys(stats.languages || {}).length}</b></div></div><article class="surface summary-panel"><p class="eyebrow">YOUR NEW ORIENTATION</p><h2>Repository summary</h2><p>${richText(summary.summary)}</p><div class="summary-lists"><div><p class="eyebrow">KEY FEATURES</p><ul>${(summary.key_features || []).map(item => `<li>${escapeHtml(item)}</li>`).join("") || "<li>No features were returned.</li>"}</ul></div><div><p class="eyebrow">TECH STACK</p><ul>${(summary.tech_stack || []).map(item => `<li>${escapeHtml(item)}</li>`).join("") || "<li>No stack details were returned.</li>"}</ul></div></div></article>`);
  } catch (error) { toast(`Repository indexed, but the overview could not load: ${error.message}`, true); }
}
async function loadQuestions() {
  if (state.questionsLoaded) return;
  const list = $("#suggested-questions"); list.innerHTML = `<div class="loading-line">Finding good places to begin…</div>`;
  try {
    const data = await request(`/api/repository/suggested-questions/${encodeURIComponent(state.repoId)}`); state.questionsLoaded = true;
    list.innerHTML = (data.questions || []).map(question => `<button class="suggested-question">${escapeHtml(question)}</button>`).join("") || `<div class="loading-line">No suggestions yet — ask your own question.</div>`;
    $$(".suggested-question", list).forEach(button => button.addEventListener("click", () => { $("#chat-input").value = button.textContent; $("#chat-input").focus(); }));
  } catch (error) { list.innerHTML = `<div class="loading-line">Suggestions are unavailable.</div>`; }
}
function appendMessage(role, content, sources = []) {
  const box = $("#chat-messages"); const welcome = $(".welcome-message", box); if (welcome) welcome.remove();
  const sourcesHtml = sources.length ? `<details class="source-details"><summary>Sources (${sources.length})</summary>${sources.map(source => `<div class="source-card"><b>${escapeHtml(source.file_path)}</b> · ${escapeHtml(source.chunk_type)} <code>${escapeHtml(source.snippet)}</code></div>`).join("")}</details>` : "";
  box.insertAdjacentHTML("beforeend", `<article class="message ${role}"><div class="message-label">${role === "user" ? "YOU" : "SOURCE & SOIL"}</div><div class="message-body">${richText(content)}${sourcesHtml}</div></article>`); box.scrollTop = box.scrollHeight;
}
async function sendChat(event) {
  event.preventDefault(); const input = $("#chat-input"), query = input.value.trim(); if (!query || !state.indexed) return;
  input.value = ""; appendMessage("user", query); state.chat.push({ role: "user", content: query }); const send = $(".send-button"); send.disabled = true;
  try { const data = await request("/api/chat/query", { method: "POST", body: JSON.stringify({ repo_id: state.repoId, query, history: state.chat.slice(0, -1).map(({role, content}) => ({role, content})) }) }); appendMessage("assistant", data.answer, data.sources || []); state.chat.push({ role: "assistant", content: data.answer }); }
  catch (error) { appendMessage("assistant", `I couldn't complete that request: ${error.message}`); }
  finally { send.disabled = false; input.focus(); }
}
function renderTree(node, prefix = "") {
  const files = (node.__files__ || []).sort().map(file => `<button class="tree-item" data-path="${escapeHtml(prefix + file)}">⌑ ${escapeHtml(file)}</button>`).join("");
  const directories = Object.entries(node).filter(([name]) => name !== "__files__").sort(([a], [b]) => a.localeCompare(b)).map(([name, child]) => `<div class="folder-name">⌄ ${escapeHtml(name)}</div><div class="tree-folder">${renderTree(child, `${prefix}${name}/`)}</div>`).join("");
  return files + directories;
}
async function loadTree() {
  if (state.treeLoaded) return;
  const tree = $("#file-tree"); tree.innerHTML = `<div class="loading-line">Loading source map…</div>`;
  try { const data = await request(`/api/repository/files/${encodeURIComponent(state.repoId)}`); state.treeLoaded = true; tree.innerHTML = renderTree(data.tree); $$(".tree-item", tree).forEach(button => button.addEventListener("click", () => loadFile(button.dataset.path, button))); }
  catch (error) { tree.innerHTML = `<div class="loading-line">Unable to load files: ${escapeHtml(error.message)}</div>`; }
}
async function loadFile(path, selected) {
  $$(".tree-item").forEach(item => item.classList.toggle("selected", item === selected)); $("#explain-path").value = path; const viewer = $("#code-viewer"); viewer.innerHTML = `<div class="loading-line">Opening ${escapeHtml(path)}…</div>`;
  try { const data = await request(`/api/repository/file-content/${encodeURIComponent(state.repoId)}?path=${encodeURIComponent(path)}`); viewer.innerHTML = `<div class="code-header"><b>${escapeHtml(data.path)}</b><span>${escapeHtml(data.language)}</span></div><pre><code>${escapeHtml(data.content)}</code></pre>`; }
  catch (error) { viewer.innerHTML = `<div class="empty-state"><span>!</span><h3>Could not open file</h3><p>${escapeHtml(error.message)}</p></div>`; }
}
async function explainFile(event) {
  event.preventDefault(); const path = $("#explain-path").value.trim(), target = $("#explanation"); if (!path) return toast("Enter a file path to explain.", true); target.classList.remove("hidden"); target.textContent = "Reading the file and preparing an explanation…";
  try { const data = await request("/api/repository/explain-file", { method: "POST", body: JSON.stringify({ repo_id: state.repoId, path }) }); target.innerHTML = richText(data.explanation); }
  catch (error) { target.textContent = error.message; }
}
async function architecture() {
  const target = $("#architecture-copy"); target.textContent = "Retrieving the architecture overview…";
  try { const data = await request(`/api/repository/architecture/${encodeURIComponent(state.repoId)}`); target.innerHTML = richText(data.overview); }
  catch (error) { target.textContent = error.message; }
}

document.addEventListener("DOMContentLoaded", () => {
  setApiLabel();
  $$(".nav-link").forEach(button => button.addEventListener("click", () => showView(button.dataset.view)));
  $$(".tab").forEach(button => button.addEventListener("click", () => showExplorerTab(button.dataset.tab)));
  $$('[data-go="setup"]').forEach(button => button.addEventListener("click", () => showView("setup")));
  $("#repository-form").addEventListener("submit", indexRepository); $("#chat-form").addEventListener("submit", sendChat); $("#explain-form").addEventListener("submit", explainFile); $("#architecture-button").addEventListener("click", architecture); $("#refresh-questions").addEventListener("click", () => { state.questionsLoaded = false; loadQuestions(); });
  $("#chat-input").addEventListener("input", event => { event.target.style.height = "auto"; event.target.style.height = `${Math.min(event.target.scrollHeight, 130)}px`; });
  const dialog = $("#api-dialog"); $("#api-toggle").addEventListener("click", () => { $("#api-url").value = apiBase; dialog.showModal(); }); $("#save-api").addEventListener("click", event => { event.preventDefault(); const url = $("#api-url").value.trim().replace(/\/$/, ""); if (!/^https?:\/\//.test(url)) return toast("Use a full http:// or https:// URL.", true); apiBase = url; localStorage.setItem("copilot-api-url", apiBase); setApiLabel(); dialog.close(); toast("API connection updated."); });
});
