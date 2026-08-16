const THINKING_PHRASES = [
  "Thinking",
  "Perplexing",
  "Considering",
  "Pondering",
  "Reasoning",
  "Working it out",
  "Drafting",
  "Connecting dots",
  "Ruminating",
];

const state = {
  conversationId: null,
  models: [],
  menuId: null,
  disabled: { xAI: [], OpenRouter: [] },
  cache: {},
  chats: [],
  pendingFiles: [],
  replaceLast: false,
  lastUser: null,
};

const streams = new Map();
let streamSeq = 0;

const $ = (id) => document.getElementById(id);

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function renderMarkdown(text) {
  const html = marked.parse(text || "", { breaks: true });
  return DOMPurify.sanitize(html, { ADD_ATTR: ["target", "rel"] });
}

function isExternalHref(href) {
  if (!href || href.startsWith("#")) return false;
  try {
    const url = new URL(href, window.location.href);
    return url.protocol === "http:" || url.protocol === "https:" || url.protocol === "mailto:";
  } catch {
    return false;
  }
}

function openExternalUrl(href) {
  if (window.pywebview?.api?.open_url) {
    window.pywebview.api.open_url(href);
    return;
  }
  window.open(href, "_blank", "noopener,noreferrer");
}

function onExternalLinkActivate(e) {
  if (e.type === "auxclick" && e.button !== 1) return;
  const anchor = e.target.closest("a[href]");
  if (!anchor || !isExternalHref(anchor.getAttribute("href"))) return;
  e.preventDefault();
  e.stopPropagation();
  openExternalUrl(anchor.href);
}

if (window.DOMPurify) {
  DOMPurify.addHook("afterSanitizeAttributes", (node) => {
    if (node.tagName === "A" && node.hasAttribute("href")) {
      node.setAttribute("target", "_blank");
      node.setAttribute("rel", "noopener noreferrer");
    }
  });
}

function formatDate(iso) {
  try {
    return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  } catch {
    return "";
  }
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const data = await res.json();
      msg = data.detail || data.message || msg;
    } catch {}
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  if (res.status === 204) return null;
  return res.json();
}

function currentMode() {
  return document.querySelector("#modes .active")?.dataset.mode || "Chat";
}

function setError(msg) {
  const el = $("error");
  if (!msg) {
    el.hidden = true;
    el.textContent = "";
    return;
  }
  el.hidden = false;
  el.textContent = msg;
}

function scrollMessages() {
  const box = $("messages");
  box.scrollTop = box.scrollHeight;
}

function showEmpty(show) {
  $("emptyState").classList.toggle("hidden", !show);
}

function streamForView() {
  for (const job of streams.values()) {
    if (job.id && job.id === state.conversationId) return job;
    if (!job.id && job.originId === state.conversationId) return job;
  }
  return null;
}

function viewingJob(job) {
  if (!job) return false;
  if (job.id) return state.conversationId === job.id;
  return state.conversationId === job.originId;
}

function lastUserWrap() {
  const nodes = [...$("messages").querySelectorAll(".msg.user")];
  return nodes[nodes.length - 1] || null;
}

function attachThinking(job, userWrap = lastUserWrap()) {
  stopJobThinking(job);
  if (!userWrap) return;
  const el = document.createElement("div");
  el.className = "thinking";
  userWrap.appendChild(el);
  job.thinkingEl = el;
  let i = Math.floor(Math.random() * THINKING_PHRASES.length);
  const tick = () => {
    if (job.thinkingEl) job.thinkingEl.textContent = `${THINKING_PHRASES[i % THINKING_PHRASES.length]}…`;
    i += 1;
  };
  tick();
  job.thinkingTimer = setInterval(tick, 1200);
}

function stopJobThinking(job) {
  if (!job) return;
  if (job.thinkingTimer) {
    clearInterval(job.thinkingTimer);
    job.thinkingTimer = null;
  }
  if (job.thinkingEl) {
    job.thinkingEl.remove();
    job.thinkingEl = null;
  }
}

function setJobStatus(job, text) {
  if (job.thinkingTimer) {
    clearInterval(job.thinkingTimer);
    job.thinkingTimer = null;
  }
  if (!job.thinkingEl && viewingJob(job)) {
    const userWrap = lastUserWrap();
    if (!userWrap) return;
    job.thinkingEl = document.createElement("div");
    job.thinkingEl.className = "thinking";
    userWrap.appendChild(job.thinkingEl);
  }
  if (job.thinkingEl) job.thinkingEl.textContent = text;
}

function renderTranscript(messages) {
  $("messages").innerHTML = "";
  const job = streamForView();
  const list = messages || [];
  const skipTail =
    job &&
    list.length &&
    list[list.length - 1].role === "assistant" &&
    !list[list.length - 1].content;
  const shown = skipTail ? list.slice(0, -1) : list;
  showEmpty(!shown.length && !job);
  for (const msg of shown) {
    addMessage(msg.role, msg.content, msg.attachments || []);
  }
  if (!job) {
    decorateLastUser();
    return;
  }
  if (job.buffer) {
    job.wrap = addMessage("assistant", job.buffer, [], { streaming: true });
    updateStreaming(job.wrap, job.buffer);
  } else {
    attachThinking(job);
  }
}

function selectSidebar(id) {
  for (const row of $("chatList").children) {
    const btn = row.querySelector(".chat-item");
    const on = btn?.dataset.id === id;
    btn?.classList.toggle("active", on);
    row.classList.toggle("active-row", on);
  }
}

function attachmentHtml(atts) {
  if (!atts?.length) return "";
  return atts
    .map((att) => {
      const src = `data:${att.mime};base64,${att.data_base64}`;
      if ((att.mime || "").startsWith("image/")) {
        return `<div class="media"><img src="${src}" alt="${escapeHtml(att.filename)}" /></div>`;
      }
      if ((att.mime || "").startsWith("video/")) {
        return `<div class="media"><video controls src="${src}"></video></div>`;
      }
      return `<div class="file-chip">${escapeHtml(att.filename || "file")}</div>`;
    })
    .join("");
}

function addMessage(role, content, attachments = [], { streaming = false } = {}) {
  const wrap = document.createElement("div");
  wrap.className = `msg ${role}`;
  const who = document.createElement("div");
  who.className = "who";
  who.textContent = role === "user" ? "You" : "Assistant";
  wrap.appendChild(who);
  if (role === "user") {
    if (content) {
      const bubble = document.createElement("div");
      bubble.className = "bubble";
      bubble.textContent = content;
      wrap.appendChild(bubble);
    }
    wrap.insertAdjacentHTML("beforeend", attachmentHtml(attachments));
  } else {
    const md = document.createElement("div");
    md.className = "md";
    md.dataset.raw = content;
    md.innerHTML = streaming ? escapeHtml(content) : renderMarkdown(content);
    wrap.appendChild(md);
    wrap.insertAdjacentHTML("beforeend", attachmentHtml(attachments));
    if (!streaming) appendSaveActions(wrap, content, attachments);
  }
  $("messages").appendChild(wrap);
  showEmpty(false);
  scrollMessages();
  return wrap;
}

function updateStreaming(wrap, text) {
  const md = wrap.querySelector(".md");
  if (!md) return;
  md.dataset.raw = text;
  md.innerHTML = renderMarkdown(text);
  scrollMessages();
}

function paintChats(conversations, selectId = state.conversationId) {
  state.chats = conversations || [];
  const nav = $("chatList");
  nav.innerHTML = "";
  for (const conv of state.chats) {
    const row = document.createElement("div");
    row.className = "chat-row" + (conv.id === selectId ? " active-row" : "");
    const btn = document.createElement("div");
    btn.className = "chat-item" + (conv.id === selectId ? " active" : "");
    btn.tabIndex = 0;
    btn.dataset.id = conv.id;
    btn.innerHTML = `<span class="title">${escapeHtml(conv.title)}</span><span class="date">${formatDate(conv.updated_at)}</span>`;
    btn.onclick = (e) => {
      if (e.detail > 1) return;
      if (e.button && e.button !== 0) return;
      if (e.target.closest(".title-input")) return;
      loadConversation(conv.id);
    };
    btn.querySelector(".title").ondblclick = (e) => {
      e.preventDefault();
      e.stopPropagation();
      startRename(conv, btn.querySelector(".title"));
    };
    btn.oncontextmenu = (e) => {
      e.preventDefault();
      e.stopPropagation();
      state.menuId = conv.id;
      const menu = $("menu");
      menu.hidden = false;
      menu.style.left = `${e.clientX}px`;
      menu.style.top = `${e.clientY}px`;
    };
    const del = document.createElement("button");
    del.type = "button";
    del.className = "chat-delete";
    del.title = "Delete";
    del.setAttribute("aria-label", "Delete chat");
    del.textContent = "×";
    del.onclick = (e) => {
      e.preventDefault();
      e.stopPropagation();
      deleteChat(conv.id);
    };
    const dots = document.createElement("span");
    dots.className = "busy-dots";
    dots.setAttribute("aria-hidden", "true");
    dots.innerHTML = "<span></span><span></span><span></span><span></span><span></span><span></span><span></span><span></span>";
    row.appendChild(btn);
    row.appendChild(dots);
    row.appendChild(del);
    nav.appendChild(row);
  }
  markBusyChats();
}

function busyIds() {
  const ids = new Set();
  for (const job of streams.values()) {
    if (job.id) ids.add(job.id);
    if (job.originId) ids.add(job.originId);
  }
  return ids;
}

function markBusyChats() {
  const ids = busyIds();
  for (const row of $("chatList").children) {
    const btn = row.querySelector(".chat-item");
    row.classList.toggle("busy", ids.has(btn?.dataset.id));
  }
}

function startRename(conv, titleEl) {
  if (!titleEl || titleEl.tagName === "INPUT") return;
  const input = document.createElement("input");
  input.className = "title-input";
  input.value = conv.title;
  titleEl.replaceWith(input);
  input.focus();
  input.select();
  let done = false;
  const finish = async (save) => {
    if (done) return;
    done = true;
    const next = input.value.trim();
    if (save && next && next !== conv.title) {
      try {
        const updated = await api(`/api/conversations/${conv.id}`, {
          method: "PATCH",
          body: JSON.stringify({ title: next }),
        });
        conv.title = updated.title;
        if (state.cache[conv.id]?.conversation) state.cache[conv.id].conversation.title = updated.title;
      } catch (err) {
        setError(err.message);
      }
    }
    const span = document.createElement("span");
    span.className = "title";
    span.textContent = conv.title;
    span.ondblclick = (e) => {
      e.preventDefault();
      e.stopPropagation();
      startRename(conv, span);
    };
    if (input.isConnected) input.replaceWith(span);
  };
  input.onkeydown = (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      finish(true);
    } else if (e.key === "Escape") {
      e.preventDefault();
      finish(false);
    }
  };
  input.onblur = () => finish(true);
}

async function refreshChats(selectId = state.conversationId) {
  const data = await api("/api/conversations");
  paintChats(data.conversations, selectId);
}

function applyConversationControls(conv) {
  const provider = conv.provider || "xAI";
  const providerChanged = $("provider").value !== provider;
  $("provider").value = provider;
  if (providerChanged || !state.models.length) {
    refreshModels().then(() => {
      if (conv.model) $("model").value = conv.model;
      applyEffort(conv.effort);
    });
  } else {
    if (conv.model) $("model").value = conv.model;
    applyEffort(conv.effort);
  }
}

async function loadConversation(id) {
  state.conversationId = id;
  selectSidebar(id);
  updateStreamChrome();
  const cached = state.cache[id];
  if (cached) {
    renderTranscript(cached.messages);
    applyConversationControls(cached.conversation);
  }
  const data = await api(`/api/conversations/${id}/messages`);
  if (state.conversationId !== id) return;
  state.cache[id] = data;
  const last = [...(data.messages || [])].reverse().find((m) => m.role === "user");
  state.lastUser = last ? { text: last.content || "", attachments: last.attachments || [] } : null;
  renderTranscript(data.messages);
  applyConversationControls(data.conversation);
  updateStreamChrome();
}

async function newChat() {
  const conv = await api("/api/conversations", {
    method: "POST",
    body: JSON.stringify({
      provider: $("provider").value,
      model: $("model").value,
      effort: $("effort").value,
    }),
  });
  state.conversationId = conv.id;
  state.cache[conv.id] = { conversation: conv, messages: [] };
  resetWorkspace({ keepId: true });
  refreshChats(conv.id);
}

function resetWorkspace({ keepId = false } = {}) {
  if (!keepId) state.conversationId = null;
  state.pendingFiles = [];
  renderPendingFiles();
  $("messages").innerHTML = "";
  showEmpty(true);
  setError("");
  updateStreamChrome();
}

async function refreshModels() {
  const provider = $("provider").value;
  const mode = currentMode().toLowerCase();
  const data = await api(`/api/models?provider=${encodeURIComponent(provider)}&mode=${encodeURIComponent(mode)}`);
  state.models = data.models || [];
  const select = $("model");
  const prev = select.value;
  select.innerHTML = state.models.map((m) => `<option value="${escapeHtml(m.id)}">${escapeHtml(m.id)}</option>`).join("");
  if (state.models.some((m) => m.id === prev)) select.value = prev;
  updateEffort();
  updateComposerReady();
}

function updateEffort() {
  const mode = currentMode();
  $("providerWrap").hidden = mode !== "Chat";
  $("effortWrap").hidden = mode !== "Chat";
  $("durationWrap").hidden = mode !== "Video";
  const chatTools = mode === "Chat";
  $("attachBtn").hidden = !chatTools;
  $("webSearchBtn").hidden = !chatTools;
  if (!chatTools) {
    $("webSearchBtn").classList.remove("active");
    state.pendingFiles = [];
    renderPendingFiles();
  }
  const info = state.models.find((m) => m.id === $("model").value);
  const effort = $("effort");
  const prev = effort.value;
  if (info?.supports_reasoning && info.reasoning_efforts?.length) {
    effort.disabled = false;
    effort.innerHTML = info.reasoning_efforts.map((e) => `<option>${e}</option>`).join("");
    if (prev && [...effort.options].some((o) => o.value === prev)) effort.value = prev;
    else if ([...effort.options].some((o) => o.value === "high")) effort.value = "high";
    else effort.value = info.reasoning_efforts[0];
  } else {
    effort.innerHTML = `<option>—</option>`;
    effort.disabled = true;
  }
}

function updateComposerReady() {
  const hasModels = state.models.length > 0;
  $("sendBtn").disabled = !hasModels || Boolean(streamForView());
  $("input").placeholder = hasModels ? "Message…" : "Add an API key in Settings…";
  const emptySub = $("emptySub");
  const emptySettings = $("emptySettings");
  if (!emptySub || !emptySettings) return;
  if (!hasModels) {
    emptySub.textContent = "Open Settings to add an API key and choose a model";
    emptySettings.hidden = false;
  } else {
    emptySub.textContent = "Type to start";
    emptySettings.hidden = true;
  }
}

function updateStreamChrome() {
  $("stopBtn").hidden = !streamForView();
  markBusyChats();
  updateComposerReady();
}

function applyEffort(value) {
  updateEffort();
  if (value && value !== "—") {
    const effort = $("effort");
    if ([...effort.options].some((o) => o.value === value)) effort.value = value;
  }
}

function paintJob(job) {
  if (!viewingJob(job) || !job.wrap?.isConnected) return;
  updateStreaming(job.wrap, job.buffer);
}

function finishJobView(job, content, attachments = []) {
  stopJobThinking(job);
  if (job.wrap?.isConnected) job.wrap.remove();
  job.wrap = null;
  if (content || attachments.length) addMessage("assistant", content, attachments);
  decorateLastUser();
}

async function sendMessage(ev) {
  ev?.preventDefault();
  if (streamForView()) return;
  const text = $("input").value.trim();
  const attachments = state.pendingFiles.slice();
  if (!text && !attachments.length) return;
  const replaceLast = state.replaceLast;
  state.replaceLast = false;
  setError("");
  $("input").value = "";
  state.pendingFiles = [];
  renderPendingFiles();
  state.lastUser = { text, attachments: attachments.slice() };
  let userWrap;
  if (replaceLast) {
    userWrap = lastUserWrap();
    if (userWrap) {
      const bubble = userWrap.querySelector(".bubble");
      if (bubble) bubble.textContent = text;
      userWrap.querySelectorAll(".file-chip, .media, .save-actions").forEach((el) => el.remove());
      if (attachments.length) userWrap.insertAdjacentHTML("beforeend", attachmentHtml(attachments));
    } else {
      userWrap = addMessage("user", text, attachments);
    }
    [...$("messages").querySelectorAll(".msg.assistant")].pop()?.remove();
  } else {
    userWrap = addMessage("user", text, attachments);
  }
  const job = {
    key: `s${++streamSeq}`,
    originId: state.conversationId,
    id: state.conversationId,
    buffer: "",
    wrap: null,
    thinkingEl: null,
    thinkingTimer: null,
    paintTimer: null,
  };
  streams.set(job.key, job);
  attachThinking(job, userWrap);
  updateStreamChrome();

  const body = {
    conversation_id: state.conversationId,
    text,
    provider: $("provider").value,
    model: $("model").value,
    mode: currentMode(),
    effort: $("effort").value,
    duration: Number($("duration").value),
    attachments,
    web_search: $("webSearchBtn").classList.contains("active"),
    replace_last: replaceLast,
  };

  try {
    const res = await fetch("/api/send", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok || !res.body) {
      let msg = "Failed to send";
      try {
        const data = await res.json();
        msg = data.detail || data.message || msg;
      } catch {}
      throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let leftover = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      leftover += decoder.decode(value, { stream: true });
      const parts = leftover.split("\n\n");
      leftover = parts.pop();
      for (const part of parts) {
        const line = part.split("\n").find((l) => l.startsWith("data: "));
        if (!line) continue;
        const event = JSON.parse(line.slice(6));
        if (event.type === "meta") {
          job.id = event.conversation_id;
          if (state.conversationId === job.originId) {
            state.conversationId = job.id;
            selectSidebar(job.id);
          }
          if (state.cache[job.id]) delete state.cache[job.id];
          markBusyChats();
        } else if (event.type === "status") {
          if (viewingJob(job)) setJobStatus(job, event.text);
        } else if (event.type === "token") {
          if (job.buffer === "") stopJobThinking(job);
          job.buffer += event.text;
          if (viewingJob(job) && !job.wrap?.isConnected) {
            job.wrap = addMessage("assistant", job.buffer, [], { streaming: true });
          }
          if (!job.paintTimer) {
            job.paintTimer = setTimeout(() => {
              paintJob(job);
              job.paintTimer = null;
            }, 50);
          }
        } else if (event.type === "media") {
          if (viewingJob(job)) finishJobView(job, event.content, event.attachments || []);
          else if (job.id) delete state.cache[job.id];
        } else if (event.type === "error") {
          if (viewingJob(job) && !job.buffer) finishJobView(job, "");
          if (viewingJob(job)) setError(event.message);
        } else if (event.type === "done") {
          if (job.paintTimer) {
            clearTimeout(job.paintTimer);
            job.paintTimer = null;
          }
          if (viewingJob(job)) {
            if (job.buffer) finishJobView(job, job.buffer, event.attachments || []);
            else finishJobView(job, "");
          } else if (job.id) {
            delete state.cache[job.id];
          }
          refreshChats(state.conversationId);
        }
      }
    }
  } catch (err) {
    if (viewingJob(job) && !job.buffer) finishJobView(job, "");
    if (viewingJob(job)) setError(err.message);
  } finally {
    if (job.paintTimer) clearTimeout(job.paintTimer);
    stopJobThinking(job);
    streams.delete(job.key);
    updateStreamChrome();
  }
}

function addAction(actions, label, onclick) {
  const btn = document.createElement("button");
  btn.className = "save";
  btn.textContent = label;
  btn.onclick = onclick;
  actions.appendChild(btn);
}

function appendSaveActions(wrap, content, attachments = []) {
  const actions = document.createElement("div");
  actions.className = "save-actions";
  addAction(actions, "Copy", () => copyText(wrap.querySelector(".md")?.dataset.raw || content || ""));
  addAction(actions, "Retry", () => retryLast());
  if (attachments?.length) {
    addAction(actions, "Save file", () => saveMessage(content, attachments));
  } else if (content) {
    addAction(actions, "Save as text", () => saveMessage(content, [], "txt"));
    addAction(actions, "Save as PDF", () => saveMessage(content, [], "pdf"));
  }
  wrap.appendChild(actions);
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }
}

function lastUserPayload() {
  const msgs = state.cache[state.conversationId]?.messages || [];
  for (let i = msgs.length - 1; i >= 0; i -= 1) {
    if (msgs[i].role === "user") {
      return { text: msgs[i].content || "", attachments: msgs[i].attachments || [] };
    }
  }
  if (state.lastUser) return { text: state.lastUser.text, attachments: state.lastUser.attachments.slice() };
  const wrap = lastUserWrap();
  return { text: wrap?.querySelector(".bubble")?.textContent || "", attachments: [] };
}

function decorateLastUser() {
  $("messages").querySelectorAll(".msg.user .edit-prompt").forEach((el) => el.remove());
  const wrap = lastUserWrap();
  if (!wrap || streamForView()) return;
  const actions = document.createElement("div");
  actions.className = "save-actions edit-prompt";
  addAction(actions, "Edit", () => {
    const payload = lastUserPayload();
    $("input").value = payload.text;
    state.pendingFiles = payload.attachments.slice();
    renderPendingFiles();
    state.replaceLast = true;
    $("input").focus();
  });
  wrap.appendChild(actions);
}

async function retryLast() {
  if (streamForView()) return;
  const payload = lastUserPayload();
  if (!payload.text && !payload.attachments.length) return;
  $("input").value = payload.text;
  state.pendingFiles = payload.attachments.slice();
  renderPendingFiles();
  state.replaceLast = true;
  await sendMessage();
}

async function saveMessage(content, attachments, format = "txt") {
  try {
    if (attachments?.length) {
      await api("/api/save", {
        method: "POST",
        body: JSON.stringify({
          title: document.querySelector(".chat-item.active .title")?.textContent || "file",
          attachments,
        }),
      });
      return;
    }
    await api("/api/save", {
      method: "POST",
      body: JSON.stringify({ content, title: "chat", format, attachments: [] }),
    });
  } catch (err) {
    setError(err.message);
  }
}

function renderChecks(container, models, provider) {
  const disabled = new Set(state.disabled[provider] || []);
  container.innerHTML = models
    .map(
      (m) =>
        `<label><input type="checkbox" data-id="${escapeHtml(m.id)}" ${disabled.has(m.id) ? "" : "checked"} /><span class="model-name">${escapeHtml(m.id)} <span class="date">(${m.kind})</span></span></label>`
    )
    .join("");
}

function collectDisabled(container) {
  return [...container.querySelectorAll("input[type=checkbox]")]
    .filter((el) => !el.checked)
    .map((el) => el.dataset.id);
}

function applyKeyCard(provider, cfg) {
  const saved = provider === "xAI" ? cfg.xai_api_key_set : cfg.openrouter_api_key_set;
  const status = provider === "xAI" ? $("xaiStatus") : $("orStatus");
  const clear = provider === "xAI" ? $("clearXai") : $("clearOr");
  const list = provider === "xAI" ? $("xaiModels") : $("orModels");
  const input = provider === "xAI" ? $("xaiKey") : $("orKey");
  status.textContent = saved ? "Key saved" : "No key";
  status.className = saved ? "hint ok" : "hint";
  clear.disabled = !saved;
  input.value = "";
  if (!saved) list.innerHTML = "";
}

async function openSettings() {
  const cfg = await api("/api/config");
  $("folder").value = cfg.output_folder;
  state.disabled = cfg.disabled_models || { xAI: [], OpenRouter: [] };
  applyKeyCard("xAI", cfg);
  applyKeyCard("OpenRouter", cfg);
  if (cfg.xai_api_key_set) {
    const data = await api("/api/models?provider=xAI&include_all=true");
    renderChecks($("xaiModels"), data.models, "xAI");
  }
  if (cfg.openrouter_api_key_set) {
    const data = await api("/api/models?provider=OpenRouter&include_all=true");
    renderChecks($("orModels"), data.models, "OpenRouter");
  }
  $("overlay").hidden = false;
}

async function testProvider(provider) {
  if (provider === "xAI" && $("xaiKey").value) {
    await api("/api/config", { method: "PUT", body: JSON.stringify({ xai_api_key: $("xaiKey").value }) });
  }
  if (provider === "OpenRouter" && $("orKey").value) {
    await api("/api/config", { method: "PUT", body: JSON.stringify({ openrouter_api_key: $("orKey").value }) });
  }
  const result = await api(`/api/test/${provider}`, { method: "POST" });
  const status = provider === "xAI" ? $("xaiStatus") : $("orStatus");
  const list = provider === "xAI" ? $("xaiModels") : $("orModels");
  status.textContent = result.message;
  status.className = result.ok ? "hint ok" : "hint err";
  if (result.ok) {
    renderChecks(list, result.models, provider);
    (provider === "xAI" ? $("clearXai") : $("clearOr")).disabled = false;
  }
}

async function clearKey(provider) {
  const body = provider === "xAI" ? { clear_xai_api_key: true } : { clear_openrouter_api_key: true };
  const cfg = await api("/api/config", { method: "PUT", body: JSON.stringify(body) });
  applyKeyCard(provider, cfg);
  await refreshModels();
}

async function saveSettings() {
  await api("/api/config", {
    method: "PUT",
    body: JSON.stringify({
      xai_api_key: $("xaiKey").value,
      openrouter_api_key: $("orKey").value,
      output_folder: $("folder").value,
      disabled_models: {
        xAI: collectDisabled($("xaiModels")),
        OpenRouter: collectDisabled($("orModels")),
      },
    }),
  });
  $("overlay").hidden = true;
  await refreshModels();
}

function guessMime(name, fallback = "application/octet-stream") {
  const ext = (name.split(".").pop() || "").toLowerCase();
  return (
    {
      png: "image/png",
      jpg: "image/jpeg",
      jpeg: "image/jpeg",
      gif: "image/gif",
      webp: "image/webp",
      pdf: "application/pdf",
      txt: "text/plain",
      md: "text/markdown",
      csv: "text/csv",
      json: "application/json",
      docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      xls: "application/vnd.ms-excel",
    }[ext] || fallback
  );
}

function readFileAsAttachment(file) {
  return new Promise((resolve, reject) => {
    if (file.size > 20 * 1024 * 1024) {
      reject(new Error(`${file.name} is larger than 20 MB.`));
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || "");
      const comma = result.indexOf(",");
      resolve({
        filename: file.name,
        mime: file.type || guessMime(file.name),
        data_base64: comma >= 0 ? result.slice(comma + 1) : result,
      });
    };
    reader.onerror = () => reject(new Error(`Could not read ${file.name}`));
    reader.readAsDataURL(file);
  });
}

async function addPendingFiles(fileList) {
  const files = [...fileList];
  if (!files.length) return;
  try {
    for (const file of files) {
      if (state.pendingFiles.length >= 10) {
        setError("Too many attachments (max 10).");
        break;
      }
      const att = await readFileAsAttachment(file);
      state.pendingFiles.push(att);
    }
    renderPendingFiles();
  } catch (err) {
    setError(err.message);
  }
}

function renderPendingFiles() {
  const box = $("attachPreview");
  if (!state.pendingFiles.length) {
    box.hidden = true;
    box.innerHTML = "";
    return;
  }
  box.hidden = false;
  box.innerHTML = state.pendingFiles
    .map((att, i) => {
      const isImage = (att.mime || "").startsWith("image/");
      const thumb = isImage
        ? `<img src="data:${att.mime};base64,${att.data_base64}" alt="" />`
        : "";
      return `<div class="attach-chip">${thumb}<span class="name">${escapeHtml(att.filename)}</span><button type="button" class="remove" data-i="${i}" aria-label="Remove">×</button></div>`;
    })
    .join("");
}

function bind() {
  $("modes").onclick = (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    [...$("modes").children].forEach((b) => b.classList.toggle("active", b === btn));
    if (btn.dataset.mode !== "Chat") $("provider").value = "xAI";
    refreshModels();
  };
  $("provider").onchange = () => {
    if ($("provider").value === "OpenRouter") {
      [...$("modes").children].forEach((b) => b.classList.toggle("active", b.dataset.mode === "Chat"));
    }
    refreshModels();
  };
  $("model").onchange = updateEffort;
  $("newChat").onclick = newChat;
  $("composer").onsubmit = sendMessage;
  $("attachBtn").onclick = () => $("fileInput").click();
  $("fileInput").onchange = (e) => {
    addPendingFiles(e.target.files);
    e.target.value = "";
  };
  $("webSearchBtn").onclick = () => $("webSearchBtn").classList.toggle("active");
  $("attachPreview").onclick = (e) => {
    const btn = e.target.closest(".remove");
    if (!btn) return;
    state.pendingFiles.splice(Number(btn.dataset.i), 1);
    renderPendingFiles();
  };
  const composer = $("composer");
  composer.addEventListener("dragover", (e) => {
    e.preventDefault();
    composer.classList.add("dragover");
  });
  composer.addEventListener("dragleave", () => composer.classList.remove("dragover"));
  composer.addEventListener("drop", (e) => {
    e.preventDefault();
    composer.classList.remove("dragover");
    if (currentMode() === "Chat") addPendingFiles(e.dataTransfer.files);
  });
  $("input").addEventListener("paste", (e) => {
    const files = [...(e.clipboardData?.files || [])];
    if (!files.length || currentMode() !== "Chat") return;
    e.preventDefault();
    addPendingFiles(files);
  });
  $("input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });
  $("stopBtn").onclick = () => {
    const job = streamForView();
    const id = job?.id || job?.originId;
    if (id) api(`/api/stop/${id}`, { method: "POST" });
  };
  $("emptySettings").onclick = openSettings;
  $("settingsBtn").onclick = openSettings;
  $("cancelSettings").onclick = () => ($("overlay").hidden = true);
  $("saveSettings").onclick = saveSettings;
  $("testXai").onclick = () => testProvider("xAI");
  $("testOr").onclick = () => testProvider("OpenRouter");
  $("clearXai").onclick = () => clearKey("xAI");
  $("clearOr").onclick = () => clearKey("OpenRouter");
  $("openFolder").onclick = () => api("/api/open-folder", { method: "POST" });
  $("browseFolder").onclick = async () => {
    if (window.pywebview?.api?.browse_folder) {
      const path = await window.pywebview.api.browse_folder();
      if (path) $("folder").value = path;
    }
  };
  $("menuDelete").onclick = async (e) => {
    e.stopPropagation();
    $("menu").hidden = true;
    if (state.menuId) await deleteChat(state.menuId);
  };
  $("menu").addEventListener("click", (e) => e.stopPropagation());
  document.addEventListener("click", (e) => {
    if (e.target.closest("#menu")) return;
    $("menu").hidden = true;
  });
  document.addEventListener("click", onExternalLinkActivate, true);
  document.addEventListener("auxclick", onExternalLinkActivate, true);
}

async function deleteChat(id) {
  $("menu").hidden = true;
  if (!id) return;
  if (!confirm("Delete this chat?")) return;
  try {
    await api(`/api/conversations/${id}`, { method: "DELETE" });
  } catch (err) {
    setError(err.message);
    return;
  }
  delete state.cache[id];
  state.menuId = null;
  for (const job of streams.values()) {
    if (job.id === id || job.originId === id) api(`/api/stop/${id}`, { method: "POST" });
  }
  const wasCurrent = state.conversationId === id;
  if (wasCurrent) state.conversationId = null;
  await refreshChats();
  if (!wasCurrent) return;
  if (state.chats.length) await loadConversation(state.chats[0].id);
  else resetWorkspace();
}

async function init() {
  bind();
  marked.setOptions({ gfm: true, breaks: true });
  await refreshModels();
  const data = await api("/api/conversations");
  paintChats(data.conversations);
  if (data.conversations.length) await loadConversation(data.conversations[0].id);
  else resetWorkspace();
}

init().catch((err) => setError(err.message));
