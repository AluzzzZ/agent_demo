const $ = (id) => document.getElementById(id);

const els = {
  sidebar: $("sidebar"), traceButton: $("traceButton"), tracePanel: $("tracePanel"),
  closeTraceButton: $("closeTraceButton"), panelScrim: $("panelScrim"),
  newSessionButton: $("newSessionButton"), sessionList: $("sessionList"),
  sessionTitle: $("sessionTitle"), chatArea: $("chatArea"),
  emptyState: $("emptyState"), messageList: $("messageList"), composerForm: $("composerForm"),
  messageInput: $("messageInput"), sendButton: $("sendButton"), composerHint: $("composerHint"),
  accountButton: $("accountButton"), accountMenu: $("accountMenu"), accountList: $("accountList"),
  accountAvatar: $("accountAvatar"), accountName: $("accountName"), accountUsername: $("accountUsername"),
  addAccountButton: $("addAccountButton"), logoutButton: $("logoutButton"),
  loginModal: $("loginModal"), loginForm: $("loginForm"), loginUsername: $("loginUsername"),
  loginPassword: $("loginPassword"), loginError: $("loginError"), loginSubmit: $("loginSubmit"),
  closeLoginButton: $("closeLoginButton"), traceEmpty: $("traceEmpty"),
  traceContent: $("traceContent"), traceSelect: $("traceSelect"),
  traceSummary: $("traceSummary"), traceEvents: $("traceEvents"), toast: $("toast"),
  collapseSidebar: $("collapseSidebar"), mobileSidebarButton: $("mobileSidebarButton"),
};

const STORAGE_ACCOUNTS = "minimal-agent.accounts.v1";
const STORAGE_ACTIVE = "minimal-agent.active-user.v1";
const state = {
  accounts: [],
  activeUserId: localStorage.getItem(STORAGE_ACTIVE),
  sessionId: null,
  sessions: [],
  traces: [],
  sending: false,
  appInfo: null,
};
const storedAccounts = readJson(STORAGE_ACCOUNTS, []);
state.accounts = Array.isArray(storedAccounts) ? storedAccounts.filter((account) => account?.token && account?.user?.user_id) : [];

function readJson(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) || fallback; } catch { return fallback; }
}

function activeAccount() {
  return state.accounts.find((account) => account.user.user_id === state.activeUserId) || null;
}

function saveAccountState() {
  localStorage.setItem(STORAGE_ACCOUNTS, JSON.stringify(state.accounts));
  if (state.activeUserId) localStorage.setItem(STORAGE_ACTIVE, state.activeUserId);
  else localStorage.removeItem(STORAGE_ACTIVE);
}

async function api(path, options = {}, token = null) {
  const account = activeAccount();
  const headers = new Headers(options.headers || {});
  if (!headers.has("Content-Type") && options.body) headers.set("Content-Type", "application/json");
  const authToken = token || account?.token;
  if (authToken) headers.set("Authorization", `Bearer ${authToken}`);
  const response = await fetch(path, { ...options, headers });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.detail || `请求失败 (${response.status})`);
    error.status = response.status;
    throw error;
  }
  return payload;
}

async function bootstrap() {
  try {
    state.appInfo = await api("/api/app-info");
  } catch { /* Login still works when hints fail. */ }

  const validAccounts = [];
  for (const account of state.accounts) {
    try {
      const user = await api("/api/auth/me", {}, account.token);
      validAccounts.push({ token: account.token, user });
    } catch { /* Drop expired local token. */ }
  }
  state.accounts = validAccounts;
  if (!state.accounts.some((account) => account.user.user_id === state.activeUserId)) {
    state.activeUserId = state.accounts[0]?.user.user_id || null;
  }
  saveAccountState();
  renderAccounts();
  if (!activeAccount()) {
    openLogin();
    return;
  }
  await activateCurrentAccount();
}

function renderAccounts() {
  const active = activeAccount();
  els.accountName.textContent = active?.user.display_name || "未登录";
  els.accountUsername.textContent = active ? `@${active.user.username}` : "选择账户";
  els.accountAvatar.textContent = initials(active?.user.display_name || "?");
  els.accountList.replaceChildren();
  for (const account of state.accounts) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `account-entry${account.user.user_id === state.activeUserId ? " active" : ""}`;
    button.setAttribute("role", "menuitem");
    const avatar = document.createElement("span");
    avatar.className = "avatar";
    avatar.textContent = initials(account.user.display_name);
    const copy = document.createElement("span");
    copy.className = "account-copy";
    const name = document.createElement("strong");
    name.textContent = account.user.display_name;
    const username = document.createElement("small");
    username.textContent = `@${account.user.username}`;
    copy.append(name, username);
    button.append(avatar, copy);
    button.addEventListener("click", async () => {
      state.activeUserId = account.user.user_id;
      saveAccountState();
      closeAccountMenu();
      renderAccounts();
      await activateCurrentAccount();
    });
    els.accountList.append(button);
  }
}

async function activateCurrentAccount() {
  state.sessionId = null;
  state.sessions = [];
  state.traces = [];
  renderSessions();
  clearChat();
  closeTrace();
  try {
    await loadSessions();
    if (state.sessions.length) await selectSession(state.sessions[0].session_id);
  } catch (error) {
    if (error.status === 401) openLogin();
    else showToast(error.message);
  }
}

function openLogin() {
  els.loginError.textContent = "";
  els.loginPassword.value = "";
  els.loginModal.classList.remove("hidden");
  setTimeout(() => els.loginUsername.focus(), 0);
}

function closeLogin() { els.loginModal.classList.add("hidden"); }
els.closeLoginButton.addEventListener("click", () => {
  if (activeAccount()) closeLogin();
  else els.loginError.textContent = "请先登录一个账户。";
});

els.loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  els.loginError.textContent = "";
  els.loginSubmit.disabled = true;
  try {
    const result = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username: els.loginUsername.value.trim(), password: els.loginPassword.value }),
    });
    const existing = state.accounts.findIndex((account) => account.user.user_id === result.user.user_id);
    if (existing >= 0) state.accounts[existing] = result;
    else state.accounts.push(result);
    state.activeUserId = result.user.user_id;
    saveAccountState();
    renderAccounts();
    closeLogin();
    await activateCurrentAccount();
  } catch (error) {
    els.loginError.textContent = error.message;
  } finally {
    els.loginSubmit.disabled = false;
  }
});

els.accountButton.addEventListener("click", () => {
  const hidden = els.accountMenu.classList.toggle("hidden");
  els.accountButton.setAttribute("aria-expanded", String(!hidden));
});
els.addAccountButton.addEventListener("click", () => { closeAccountMenu(); openLogin(); });
els.logoutButton.addEventListener("click", async () => {
  const account = activeAccount();
  if (!account) return;
  try { await api("/api/auth/logout", { method: "POST" }); } catch { /* Local cleanup still applies. */ }
  state.accounts = state.accounts.filter((item) => item.user.user_id !== account.user.user_id);
  state.activeUserId = state.accounts[0]?.user.user_id || null;
  saveAccountState();
  closeAccountMenu();
  renderAccounts();
  if (activeAccount()) await activateCurrentAccount(); else { clearChat(); renderSessions(); openLogin(); }
});

function closeAccountMenu() {
  els.accountMenu.classList.add("hidden");
  els.accountButton.setAttribute("aria-expanded", "false");
}

async function loadSessions() {
  state.sessions = await api("/api/sessions");
  renderSessions();
}

function renderSessions() {
  els.sessionList.replaceChildren();
  if (!state.sessions.length) {
    const empty = document.createElement("div");
    empty.className = "session-empty";
    empty.textContent = activeAccount() ? "还没有任务。点击“新任务”开始。" : "登录后查看任务。";
    els.sessionList.append(empty);
    return;
  }
  for (const session of state.sessions) {
    const row = document.createElement("div");
    row.className = "session-row";
    const button = document.createElement("button");
    button.type = "button";
    button.className = `session-item${session.session_id === state.sessionId ? " active" : ""}`;
    const title = document.createElement("span");
    title.className = "session-item-title";
    title.textContent = session.title || "新对话";
    const meta = document.createElement("span");
    meta.className = "session-item-meta";
    const count = document.createElement("span");
    count.textContent = `${session.message_count || 0} 条记录`;
    const date = document.createElement("span");
    date.textContent = shortDate(session.updated_at);
    meta.append(count, date);
    button.append(title, meta);
    button.addEventListener("click", () => selectSession(session.session_id));
    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "session-delete";
    deleteButton.textContent = "删除";
    deleteButton.title = `删除“${session.title || "新对话"}”`;
    deleteButton.setAttribute("aria-label", deleteButton.title);
    deleteButton.addEventListener("click", (event) => {
      event.stopPropagation();
      deleteSession(session).catch((error) => showToast(error.message));
    });
    row.append(button, deleteButton);
    els.sessionList.append(row);
  }
}

async function deleteSession(session) {
  if (state.sending && state.sessionId === session.session_id) {
    showToast("当前任务仍在执行，请完成后再删除。");
    return;
  }
  if (!window.confirm(`确定删除“${session.title || "新对话"}”吗？消息、待办和 Trace 将一并删除。`)) return;
  await api(`/api/sessions/${session.session_id}`, { method: "DELETE" });
  const wasSelected = state.sessionId === session.session_id;
  if (wasSelected) {
    state.sessionId = null;
    closeTrace();
    clearChat();
  }
  await loadSessions();
  if (wasSelected && state.sessions.length) await selectSession(state.sessions[0].session_id);
  showToast("任务已删除。");
}

async function createSession() {
  if (!activeAccount()) { openLogin(); return null; }
  const session = await api("/api/sessions", { method: "POST", body: JSON.stringify({ title: "新对话" }) });
  await loadSessions();
  await selectSession(session.session_id);
  els.messageInput.focus();
  return session.session_id;
}

async function selectSession(sessionId) {
  state.sessionId = sessionId;
  renderSessions();
  const session = state.sessions.find((item) => item.session_id === sessionId);
  els.sessionTitle.textContent = session?.title || "新对话";
  setSending(true, "正在读取会话…");
  try {
    const [messages] = await Promise.all([loadMessages(), refreshTraces()]);
    renderMessages(messages);
  } catch (error) { showToast(error.message); }
  finally { setSending(false); }
  els.sidebar.classList.remove("mobile-open");
}

async function loadMessages() {
  if (!state.sessionId) return [];
  return api(`/api/sessions/${state.sessionId}/messages`);
}

function clearChat() {
  els.messageList.replaceChildren();
  els.emptyState.classList.remove("hidden");
  els.sessionTitle.textContent = "新任务";
}

function renderMessages(messages) {
  els.messageList.replaceChildren();
  const visible = messages.some((message) => message.content);
  els.emptyState.classList.toggle("hidden", visible);
  if (!visible) return;
  const toolNames = new Map();
  for (const message of messages) {
    if (message.role !== "assistant" || !Array.isArray(message.content)) continue;
    for (const block of message.content) if (block?.type === "tool_use") toolNames.set(block.id, block.name);
  }
  for (const message of messages) {
    if (typeof message.content === "string") {
      appendMessage(message.role, message.content, message.created_at);
      continue;
    }
    if (!Array.isArray(message.content)) continue;
    if (message.role === "assistant") appendAssistantBlocks(message.content, message.created_at);
    else appendToolResults(message.content, toolNames, message.created_at);
  }
  scrollChat();
}

function appendMessage(role, text, createdAt, extraClass = "") {
  const row = document.createElement("article");
  row.className = `message ${role}${extraClass ? ` ${extraClass}` : ""}`;
  if (role !== "user") {
    const avatar = document.createElement("span"); avatar.className = "message-avatar"; avatar.textContent = "A"; row.append(avatar);
  }
  const body = document.createElement("div"); body.className = "message-body";
  const copy = document.createElement("div"); copy.className = "message-text";
  if (role === "assistant") renderMarkdown(copy, text);
  else copy.textContent = text;
  body.append(copy);
  if (createdAt) { const time = document.createElement("div"); time.className = "message-time"; time.textContent = formatTime(createdAt); body.append(time); }
  row.append(body); els.messageList.append(row); return body;
}

function appendAssistantBlocks(blocks, createdAt) {
  const row = document.createElement("article"); row.className = "message assistant";
  const avatar = document.createElement("span"); avatar.className = "message-avatar"; avatar.textContent = "A";
  const body = document.createElement("div"); body.className = "message-body";
  for (const block of blocks) {
    if (block?.type === "text" && block.text) { const text = document.createElement("div"); text.className = "message-text"; renderMarkdown(text, block.text); body.append(text); }
    if (block?.type === "tool_use") body.append(toolCard(`调用 ${block.name}`, block.input, false, "已提交"));
  }
  if (createdAt) { const time = document.createElement("div"); time.className = "message-time"; time.textContent = formatTime(createdAt); body.append(time); }
  row.append(avatar, body); els.messageList.append(row);
}

function appendToolResults(blocks, toolNames, createdAt) {
  const results = blocks.filter((block) => block?.type === "tool_result");
  if (!results.length) return;
  const row = document.createElement("article"); row.className = "message assistant tool-message";
  const avatar = document.createElement("span"); avatar.className = "message-avatar"; avatar.textContent = "⌁";
  const body = document.createElement("div"); body.className = "message-body";
  for (const block of results) {
    const name = toolNames.get(block.tool_use_id) || "tool";
    body.append(toolCard(`${name} 结果`, parseMaybeJson(block.content), Boolean(block.is_error), block.is_error ? "失败" : "完成"));
  }
  if (createdAt) { const time = document.createElement("div"); time.className = "message-time"; time.textContent = formatTime(createdAt); body.append(time); }
  row.append(avatar, body); els.messageList.append(row);
}

function toolCard(title, payload, isError, statusText) {
  const details = document.createElement("details"); details.className = `tool-card${isError ? " error" : ""}`;
  const summary = document.createElement("summary");
  const label = document.createElement("span"); label.textContent = title;
  const status = document.createElement("span"); status.className = "tool-state"; status.textContent = statusText;
  const pre = document.createElement("pre"); pre.textContent = typeof payload === "string" ? payload : JSON.stringify(payload, null, 2);
  summary.append(label, status); details.append(summary, pre); return details;
}

function parseMaybeJson(value) { try { return JSON.parse(value); } catch { return value; } }

function renderMarkdown(container, source) {
  container.classList.add("markdown-body");
  const lines = String(source || "").replace(/\r\n?/g, "\n").split("\n");
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) { index += 1; continue; }

    const fence = line.match(/^\s*```([\w-]*)\s*$/);
    if (fence) {
      const codeLines = [];
      index += 1;
      while (index < lines.length && !/^\s*```\s*$/.test(lines[index])) {
        codeLines.push(lines[index]); index += 1;
      }
      if (index < lines.length) index += 1;
      const pre = document.createElement("pre"); pre.className = "markdown-code";
      const code = document.createElement("code"); code.textContent = codeLines.join("\n");
      if (fence[1]) code.dataset.language = fence[1];
      pre.append(code); container.append(pre); continue;
    }

    const heading = line.match(/^\s*(#{1,6})\s+(.+)$/);
    if (heading) {
      const node = document.createElement(`h${heading[1].length}`);
      appendInlineMarkdown(node, heading[2]); container.append(node); index += 1; continue;
    }

    if (/^\s*([-*_])(?:\s*\1){2,}\s*$/.test(line)) {
      container.append(document.createElement("hr")); index += 1; continue;
    }

    if (index + 1 < lines.length && lines[index].includes("|") && isTableDivider(lines[index + 1])) {
      const headers = splitTableRow(lines[index]);
      index += 2;
      const table = document.createElement("table");
      const head = document.createElement("thead");
      const headRow = document.createElement("tr");
      for (const value of headers) { const cell = document.createElement("th"); appendInlineMarkdown(cell, value); headRow.append(cell); }
      head.append(headRow); table.append(head);
      const body = document.createElement("tbody");
      while (index < lines.length && lines[index].trim() && lines[index].includes("|")) {
        const row = document.createElement("tr");
        for (const value of splitTableRow(lines[index])) { const cell = document.createElement("td"); appendInlineMarkdown(cell, value); row.append(cell); }
        body.append(row); index += 1;
      }
      table.append(body);
      const wrapper = document.createElement("div"); wrapper.className = "markdown-table-wrap"; wrapper.append(table); container.append(wrapper); continue;
    }

    const unordered = line.match(/^\s*[-+*]\s+(.+)$/);
    const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
    if (unordered || ordered) {
      const list = document.createElement(unordered ? "ul" : "ol");
      const pattern = unordered ? /^\s*[-+*]\s+(.+)$/ : /^\s*\d+[.)]\s+(.+)$/;
      while (index < lines.length) {
        const item = lines[index].match(pattern);
        if (!item) break;
        const node = document.createElement("li"); appendInlineMarkdown(node, item[1]); list.append(node); index += 1;
      }
      container.append(list); continue;
    }

    if (/^\s*>\s?/.test(line)) {
      const quoteLines = [];
      while (index < lines.length && /^\s*>\s?/.test(lines[index])) {
        quoteLines.push(lines[index].replace(/^\s*>\s?/, "")); index += 1;
      }
      const quote = document.createElement("blockquote"); appendInlineMarkdown(quote, quoteLines.join("\n")); container.append(quote); continue;
    }

    const paragraphLines = [line.trim()];
    index += 1;
    while (index < lines.length && lines[index].trim() && !startsMarkdownBlock(lines, index)) {
      paragraphLines.push(lines[index].trim()); index += 1;
    }
    const paragraph = document.createElement("p"); appendInlineMarkdown(paragraph, paragraphLines.join("\n")); container.append(paragraph);
  }
}

function startsMarkdownBlock(lines, index) {
  const value = lines[index];
  return /^\s*```/.test(value) || /^\s*#{1,6}\s+/.test(value) ||
    /^\s*([-*_])(?:\s*\1){2,}\s*$/.test(value) || /^\s*[-+*]\s+/.test(value) ||
    /^\s*\d+[.)]\s+/.test(value) || /^\s*>\s?/.test(value) ||
    (index + 1 < lines.length && value.includes("|") && isTableDivider(lines[index + 1]));
}

function splitTableRow(line) {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((value) => value.trim());
}

function isTableDivider(line) {
  const cells = splitTableRow(line);
  return cells.length > 1 && cells.every((value) => /^:?-{3,}:?$/.test(value));
}

function appendInlineMarkdown(container, source) {
  const value = String(source);
  const pattern = /(`[^`\n]+`|\*\*[^*\n]+\*\*|\[[^\]\n]+\]\(https?:\/\/[^)\s]+\)|\n)/g;
  let cursor = 0;
  for (const match of value.matchAll(pattern)) {
    if (match.index > cursor) container.append(document.createTextNode(value.slice(cursor, match.index)));
    const token = match[0];
    if (token === "\n") container.append(document.createElement("br"));
    else if (token.startsWith("**")) { const strong = document.createElement("strong"); strong.textContent = token.slice(2, -2); container.append(strong); }
    else if (token.startsWith("`")) { const code = document.createElement("code"); code.textContent = token.slice(1, -1); container.append(code); }
    else {
      const linkParts = token.match(/^\[([^\]]+)\]\((https?:\/\/[^)]+)\)$/);
      if (linkParts) { const link = document.createElement("a"); link.textContent = linkParts[1]; link.href = linkParts[2]; link.target = "_blank"; link.rel = "noopener noreferrer"; container.append(link); }
    }
    cursor = match.index + token.length;
  }
  if (cursor < value.length) container.append(document.createTextNode(value.slice(cursor)));
}

function appendTyping() {
  const body = appendMessage("assistant", "", null, "pending");
  const typing = document.createElement("span"); typing.className = "typing";
  typing.append(document.createElement("i"), document.createElement("i"), document.createElement("i"));
  body.append(typing); scrollChat();
}

els.composerForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (state.sending) return;
  const message = els.messageInput.value.trim();
  if (!message) return;
  if (!activeAccount()) { openLogin(); return; }
  if (!state.sessionId) await createSession();
  els.messageInput.value = ""; resizeTextarea();
  els.emptyState.classList.add("hidden");
  appendMessage("user", message); appendTyping();
  setSending(true, "Agent 正在运行 Loop…");
  try {
    const result = await api(`/api/sessions/${state.sessionId}/chat`, { method: "POST", body: JSON.stringify({ message }) });
    await loadSessions();
    const messages = await loadMessages(); renderMessages(messages);
    await refreshTraces(result.trace_id);
  } catch (error) {
    const messages = await loadMessages().catch(() => []); renderMessages(messages);
    appendMessage("assistant", `请求失败：${error.message}`);
    await refreshTraces().catch(() => {});
    showToast(error.message);
  } finally { setSending(false); els.messageInput.focus(); }
});

function setSending(value, hint = "Enter 发送 · Shift Enter 换行") {
  state.sending = value; els.sendButton.disabled = value; els.messageInput.disabled = value; els.composerHint.textContent = hint;
}

els.messageInput.addEventListener("input", resizeTextarea);
els.messageInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); els.composerForm.requestSubmit(); }
});
function resizeTextarea() { els.messageInput.style.height = "auto"; els.messageInput.style.height = `${Math.min(els.messageInput.scrollHeight, 160)}px`; }

els.newSessionButton.addEventListener("click", () => createSession().catch((error) => showToast(error.message)));
document.querySelectorAll(".prompt-card").forEach((button) => button.addEventListener("click", async () => {
  if (!state.sessionId) await createSession(); els.messageInput.value = button.dataset.prompt || ""; resizeTextarea(); els.messageInput.focus();
}));
document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") { event.preventDefault(); createSession().catch((error) => showToast(error.message)); }
  if (event.key === "Escape") { closeTrace(); closeAccountMenu(); }
});

els.traceButton.addEventListener("click", () => els.tracePanel.classList.contains("open") ? closeTrace() : openTrace());
els.closeTraceButton.addEventListener("click", (event) => closeTrace(event));
els.panelScrim.addEventListener("click", (event) => closeTrace(event));
function openTrace() {
  els.tracePanel.classList.add("open"); els.tracePanel.setAttribute("aria-hidden", "false");
  document.body.classList.add("trace-open");
  els.traceButton.classList.add("active"); els.panelScrim.classList.remove("hidden"); refreshTraces().catch((error) => showToast(error.message));
}
function closeTrace(event = null) {
  event?.preventDefault();
  event?.stopPropagation();
  els.tracePanel.classList.remove("open"); els.tracePanel.setAttribute("aria-hidden", "true"); els.traceButton.classList.remove("active"); els.panelScrim.classList.add("hidden");
  document.body.classList.remove("trace-open");
}

async function refreshTraces(preferredTraceId = null) {
  if (!state.sessionId) { renderTraceEmpty(); return; }
  state.traces = await api(`/api/sessions/${state.sessionId}/traces`);
  if (!state.traces.length) { renderTraceEmpty(); return; }
  els.traceEmpty.classList.add("hidden"); els.traceContent.classList.remove("hidden"); els.traceSelect.replaceChildren();
  for (const trace of state.traces) {
    const option = document.createElement("option"); option.value = trace.trace_id;
    option.textContent = `${formatTime(trace.started_at)} · ${trace.status} · ${trace.tools.join(", ") || "直接回答"}`;
    els.traceSelect.append(option);
  }
  const target = preferredTraceId && state.traces.some((trace) => trace.trace_id === preferredTraceId) ? preferredTraceId : state.traces[0].trace_id;
  els.traceSelect.value = target; await loadTrace(target);
}
function renderTraceEmpty() { els.traceEmpty.classList.remove("hidden"); els.traceContent.classList.add("hidden"); }
els.traceSelect.addEventListener("change", () => loadTrace(els.traceSelect.value).catch((error) => showToast(error.message)));

async function loadTrace(traceId) {
  const detail = await api(`/api/traces/${traceId}`); const events = detail.events || [];
  const modelCalls = events.filter((event) => event.event === "llm_started").length;
  const toolCalls = events.filter((event) => event.event === "tool_started").length;
  const started = events[0]?.timestamp ? new Date(events[0].timestamp).getTime() : 0;
  const finished = events.at(-1)?.timestamp ? new Date(events.at(-1).timestamp).getTime() : started;
  els.traceSummary.replaceChildren(traceStat("模型调用", modelCalls), traceStat("工具调用", toolCalls), traceStat("总耗时", `${Math.max(finished - started, 0)} ms`));
  els.traceEvents.replaceChildren();
  for (const event of events) {
    const card = document.createElement("div");
    const category = event.event.includes("tool") ? "tool" : event.event.includes("llm") ? "llm" : event.event.includes("failed") || event.status === "error" ? "error" : "";
    card.className = `trace-event ${category}`;
    const head = document.createElement("div"); head.className = "trace-event-head";
    const name = document.createElement("span"); name.className = "trace-event-name"; name.textContent = `${event.sequence_no || "·"}. ${event.event}`;
    const time = document.createElement("span"); time.className = "trace-event-time"; time.textContent = formatTime(event.timestamp);
    head.append(name, time); card.append(head);
    const parts = [];
    if (event.iteration) parts.push(`iteration=${event.iteration}`);
    if (event.tool) parts.push(`tool=${event.tool}`);
    if (event.duration_ms != null) parts.push(`duration=${event.duration_ms}ms`);
    if (event.status) parts.push(`status=${event.status}`);
    if (event.error_code) parts.push(`error=${event.error_code}`);
    if (event.context_characters) parts.push(`context=${event.context_characters} chars`);
    if (parts.length) { const meta = document.createElement("div"); meta.className = "trace-event-meta"; meta.textContent = parts.join(" · "); card.append(meta); }
    els.traceEvents.append(card);
  }
}
function traceStat(label, value) { const box = document.createElement("div"); box.className = "trace-stat"; const small = document.createElement("small"); small.textContent = label; const strong = document.createElement("strong"); strong.textContent = value; box.append(small, strong); return box; }

els.mobileSidebarButton.addEventListener("click", () => els.sidebar.classList.add("mobile-open"));
els.collapseSidebar.addEventListener("click", () => els.sidebar.classList.remove("mobile-open"));

function initials(value) { return value.trim().split(/\s+/).map((part) => part[0]).join("").slice(0, 2).toUpperCase() || "?"; }
function formatTime(value) { if (!value) return ""; const normalized = String(value).includes("T") ? value : `${String(value).replace(" ", "T")}Z`; const date = new Date(normalized); return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }); }
function shortDate(value) { if (!value) return ""; const date = new Date(`${String(value).replace(" ", "T")}Z`); return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" }); }
function scrollChat() {
  requestAnimationFrame(() => requestAnimationFrame(() => {
    els.chatArea.scrollTop = els.chatArea.scrollHeight;
  }));
}
let toastTimer; function showToast(message) { clearTimeout(toastTimer); els.toast.textContent = message; els.toast.classList.remove("hidden"); toastTimer = setTimeout(() => els.toast.classList.add("hidden"), 4200); }

bootstrap();
