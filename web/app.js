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
  sending: false,
  models: [],
  menuId: null,
  disabled: { xAI: [], OpenRouter: [] },
  cache: {},
  chats: [],
};

let thinkingTimer = null;

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
  return DOMPurify.sanitize(html);
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

function renderTranscript(messages) {
  $("messages").innerHTML = "";
  showEmpty(!messages?.length);
  for (const msg of messages || []) {
    addMessage(msg.role, msg.content, msg.attachments || []);
  }
}

function selectSidebar(id) {
  for (const btn of $("chatList").children) {
    btn.classList.toggle("active", btn.dataset.id === id);
  }
}

function startThinking() {
  stopThinking();
  const el = $("status");
  let i = Math.floor(Math.random() * THINKING_PHRASES.length);
  const tick = () => {
    el.hidden = false;
    el.textContent = `${THINKING_PHRASES[i % THINKING_PHRASES.length]}…`;
    i += 1;
  };
  tick();
  thinkingTimer = setInterval(tick, 1200);
}

function stopThinking() {
  if (thinkingTimer) {
    clearInterval(thinkingTimer);
    thinkingTimer = null;
  }
  const el = $("status");
  el.hidden = true;
  el.textContent = "";
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
      return `<div class="media">${escapeHtml(att.filename)}</div>`;
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
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.textContent = content;
    wrap.appendChild(bubble);
  } else {
    const md = document.createElement("div");
    md.className = "md";
    md.dataset.raw = content;
    md.innerHTML = streaming ? escapeHtml(content) : renderMarkdown(content);
    wrap.appendChild(md);
    wrap.insertAdjacentHTML("beforeend", attachmentHtml(attachments));
    const save = document.createElement("button");
    save.className = "save";
    save.textContent = attachments?.length ? "Save file" : "Save as text";
    save.onclick = () => saveMessage(content, attachments);
    wrap.appendChild(save);
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
    const btn = document.createElement("button");
    btn.className = "chat-item" + (conv.id === selectId ? " active" : "");
    btn.dataset.id = conv.id;
    btn.innerHTML = `<span class="title">${escapeHtml(conv.title)}</span><span class="date">${formatDate(conv.updated_at)}</span>`;
    btn.onclick = () => loadConversation(conv.id);
    btn.oncontextmenu = (e) => {
      e.preventDefault();
      state.menuId = conv.id;
      const menu = $("menu");
      menu.hidden = false;
      menu.style.left = `${e.clientX}px`;
      menu.style.top = `${e.clientY}px`;
    };
    nav.appendChild(btn);
  }
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
  if (state.sending) return;
  state.conversationId = id;
  selectSidebar(id);
  const cached = state.cache[id];
  if (cached) {
    renderTranscript(cached.messages);
    applyConversationControls(cached.conversation);
  }
  const data = await api(`/api/conversations/${id}/messages`);
  if (state.conversationId !== id) return;
  state.cache[id] = data;
  renderTranscript(data.messages);
  applyConversationControls(data.conversation);
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
  $("messages").innerHTML = "";
  showEmpty(true);
  setError("");
  stopThinking();
  refreshChats(conv.id);
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
}

function updateEffort() {
  const mode = currentMode();
  $("effortWrap").hidden = mode !== "Chat";
  $("durationWrap").hidden = mode !== "Video";
  const info = state.models.find((m) => m.id === $("model").value);
  const effort = $("effort");
  if (info?.supports_reasoning && info.reasoning_efforts?.length) {
    effort.disabled = false;
    effort.innerHTML = info.reasoning_efforts.map((e) => `<option>${e}</option>`).join("");
    if (![...effort.options].some((o) => o.value === "high")) effort.value = info.reasoning_efforts[0];
    else effort.value = "high";
  } else {
    effort.innerHTML = `<option>—</option>`;
    effort.disabled = true;
  }
}

function applyEffort(value) {
  updateEffort();
  if (value && value !== "—") {
    const effort = $("effort");
    if ([...effort.options].some((o) => o.value === value)) effort.value = value;
  }
}

let streamTimer = null;
let streamBuffer = "";

async function sendMessage(ev) {
  ev?.preventDefault();
  if (state.sending) return;
  const text = $("input").value.trim();
  if (!text) return;
  setError("");
  $("input").value = "";
  addMessage("user", text);
  const wrap = addMessage("assistant", "", [], { streaming: true });
  state.sending = true;
  $("sendBtn").disabled = true;
  $("stopBtn").disabled = false;
  streamBuffer = "";
  startThinking();

  const body = {
    conversation_id: state.conversationId,
    text,
    provider: $("provider").value,
    model: $("model").value,
    mode: currentMode(),
    effort: $("effort").value,
    duration: Number($("duration").value),
  };

  try {
    const res = await fetch("/api/send", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok || !res.body) throw new Error("Failed to send");
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
          state.conversationId = event.conversation_id;
        } else if (event.type === "token") {
          if (streamBuffer === "") stopThinking();
          streamBuffer += event.text;
          if (!streamTimer) {
            streamTimer = setTimeout(() => {
              updateStreaming(wrap, streamBuffer);
              streamTimer = null;
            }, 50);
          }
        } else if (event.type === "media") {
          stopThinking();
          wrap.remove();
          addMessage("assistant", event.content, event.attachments || []);
        } else if (event.type === "error") {
          stopThinking();
          setError(event.message);
        } else if (event.type === "done") {
          stopThinking();
          if (streamTimer) {
            clearTimeout(streamTimer);
            streamTimer = null;
          }
          if (streamBuffer) {
            wrap.remove();
            addMessage("assistant", streamBuffer, event.attachments || []);
          }
          refreshChats(state.conversationId);
        }
      }
    }
  } catch (err) {
    stopThinking();
    setError(err.message);
  } finally {
    stopThinking();
    state.sending = false;
    $("sendBtn").disabled = false;
    $("stopBtn").disabled = true;
  }
}

async function saveMessage(content, attachments) {
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
    const format = confirm("Save as PDF?\nOK = PDF, Cancel = text") ? "pdf" : "txt";
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

async function openSettings() {
  const cfg = await api("/api/config");
  $("folder").value = cfg.output_folder;
  state.disabled = cfg.disabled_models || { xAI: [], OpenRouter: [] };
  $("xaiStatus").textContent = cfg.xai_api_key_set ? "xAI key is saved" : "";
  $("orStatus").textContent = cfg.openrouter_api_key_set ? "OpenRouter key is saved" : "";
  $("xaiKey").value = "";
  $("orKey").value = "";
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
  status.style.color = result.ok ? "#15803d" : "#b91c1c";
  if (result.ok) renderChecks(list, result.models, provider);
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
  $("input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });
  $("stopBtn").onclick = () => {
    stopThinking();
    if (state.conversationId) api(`/api/stop/${state.conversationId}`, { method: "POST" });
  };
  $("settingsBtn").onclick = openSettings;
  $("cancelSettings").onclick = () => ($("overlay").hidden = true);
  $("saveSettings").onclick = saveSettings;
  $("testXai").onclick = () => testProvider("xAI");
  $("testOr").onclick = () => testProvider("OpenRouter");
  $("openFolder").onclick = () => api("/api/open-folder", { method: "POST" });
  $("browseFolder").onclick = async () => {
    if (window.pywebview?.api?.browse_folder) {
      const path = await window.pywebview.api.browse_folder();
      if (path) $("folder").value = path;
    }
  };
  $("menuDelete").onclick = async () => {
    $("menu").hidden = true;
    if (!state.menuId) return;
    if (!confirm("Delete this chat?")) return;
    await api(`/api/conversations/${state.menuId}`, { method: "DELETE" });
    delete state.cache[state.menuId];
    if (state.conversationId === state.menuId) await newChat();
    else refreshChats();
  };
  document.addEventListener("click", () => ($("menu").hidden = true));
}

async function init() {
  bind();
  marked.setOptions({ gfm: true, breaks: true });
  await refreshModels();
  const data = await api("/api/conversations");
  paintChats(data.conversations);
  if (data.conversations.length) await loadConversation(data.conversations[0].id);
  else await newChat();
}

init().catch((err) => setError(err.message));
