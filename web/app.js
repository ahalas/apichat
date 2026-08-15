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
  streamId: null,
  models: [],
  menuId: null,
  disabled: { xAI: [], OpenRouter: [] },
  cache: {},
  chats: [],
  pendingFiles: [],
};

let thinkingTimer = null;
let streamTimer = null;
let streamBuffer = "";
let streamWrap = null;
let streamOriginId = null;

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

function viewingStream() {
  if (!state.sending) return false;
  if (state.streamId) return state.conversationId === state.streamId;
  return state.conversationId === streamOriginId;
}

function renderTranscript(messages) {
  $("messages").innerHTML = "";
  const list = messages || [];
  const skipTail =
    viewingStream() &&
    list.length &&
    list[list.length - 1].role === "assistant" &&
    !list[list.length - 1].content;
  const shown = skipTail ? list.slice(0, -1) : list;
  showEmpty(!shown.length && !viewingStream());
  for (const msg of shown) {
    addMessage(msg.role, msg.content, msg.attachments || []);
  }
  if (viewingStream()) {
    streamWrap = addMessage("assistant", streamBuffer, [], { streaming: true });
    if (streamBuffer) updateStreaming(streamWrap, streamBuffer);
  }
}

function selectSidebar(id) {
  for (const row of $("chatList").children) {
    const btn = row.matches(".chat-item") ? row : row.querySelector(".chat-item");
    btn?.classList.toggle("active", btn.dataset.id === id);
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
    row.className = "chat-row";
    const btn = document.createElement("button");
    btn.className = "chat-item" + (conv.id === selectId ? " active" : "");
    btn.dataset.id = conv.id;
    btn.innerHTML = `<span class="title">${escapeHtml(conv.title)}</span><span class="date">${formatDate(conv.updated_at)}</span>`;
    btn.onclick = (e) => {
      if (e.button && e.button !== 0) return;
      loadConversation(conv.id);
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
    row.appendChild(btn);
    row.appendChild(del);
    nav.appendChild(row);
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
  streamWrap = null;
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
  $("sendBtn").disabled = !hasModels || state.sending;
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
  $("stopBtn").hidden = !state.sending;
  if (viewingStream()) {
    if (!streamBuffer && state.sending && !thinkingTimer) startThinking();
  } else if (thinkingTimer || !$("status").hidden) {
    const el = $("status");
    el.hidden = true;
    el.textContent = "";
    if (thinkingTimer) {
      clearInterval(thinkingTimer);
      thinkingTimer = null;
    }
  }
  updateComposerReady();
}

function applyEffort(value) {
  updateEffort();
  if (value && value !== "—") {
    const effort = $("effort");
    if ([...effort.options].some((o) => o.value === value)) effort.value = value;
  }
}

function paintStream() {
  if (!viewingStream() || !streamWrap?.isConnected) return;
  updateStreaming(streamWrap, streamBuffer);
}

function finishStreamView(content, attachments = []) {
  if (streamWrap?.isConnected) streamWrap.remove();
  streamWrap = null;
  if (content || attachments.length) addMessage("assistant", content, attachments);
}

async function sendMessage(ev) {
  ev?.preventDefault();
  if (state.sending) return;
  const text = $("input").value.trim();
  const attachments = state.pendingFiles.slice();
  if (!text && !attachments.length) return;
  setError("");
  $("input").value = "";
  state.pendingFiles = [];
  renderPendingFiles();
  addMessage("user", text, attachments);
  streamWrap = addMessage("assistant", "", [], { streaming: true });
  streamOriginId = state.conversationId;
  state.streamId = state.conversationId;
  state.sending = true;
  streamBuffer = "";
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
          state.streamId = event.conversation_id;
          if (state.conversationId === streamOriginId) {
            state.conversationId = event.conversation_id;
            selectSidebar(event.conversation_id);
          }
          if (state.cache[event.conversation_id]) delete state.cache[event.conversation_id];
        } else if (event.type === "status") {
          if (!viewingStream()) continue;
          if (thinkingTimer) {
            clearInterval(thinkingTimer);
            thinkingTimer = null;
          }
          const el = $("status");
          el.hidden = false;
          el.textContent = event.text;
        } else if (event.type === "token") {
          if (streamBuffer === "") stopThinking();
          streamBuffer += event.text;
          if (!streamTimer) {
            streamTimer = setTimeout(() => {
              paintStream();
              streamTimer = null;
            }, 50);
          }
        } else if (event.type === "media") {
          stopThinking();
          if (viewingStream()) finishStreamView(event.content, event.attachments || []);
          else if (state.streamId) delete state.cache[state.streamId];
        } else if (event.type === "error") {
          stopThinking();
          if (viewingStream() && !streamBuffer) finishStreamView("");
          if (viewingStream() || !state.conversationId) setError(event.message);
        } else if (event.type === "done") {
          stopThinking();
          if (streamTimer) {
            clearTimeout(streamTimer);
            streamTimer = null;
          }
          if (viewingStream()) {
            if (streamBuffer) finishStreamView(streamBuffer, event.attachments || []);
            else if (streamWrap?.isConnected) streamWrap.remove();
          } else if (state.streamId) {
            delete state.cache[state.streamId];
          }
          refreshChats(state.conversationId);
        }
      }
    }
  } catch (err) {
    stopThinking();
    if (viewingStream() && !streamBuffer) finishStreamView("");
    if (viewingStream() || !state.conversationId) setError(err.message);
  } finally {
    stopThinking();
    state.sending = false;
    state.streamId = null;
    streamOriginId = null;
    streamWrap = null;
    streamBuffer = "";
    updateStreamChrome();
  }
}

function appendSaveActions(wrap, content, attachments = []) {
  const actions = document.createElement("div");
  actions.className = "save-actions";
  if (attachments?.length) {
    const save = document.createElement("button");
    save.className = "save";
    save.textContent = "Save file";
    save.onclick = () => saveMessage(content, attachments);
    actions.appendChild(save);
  } else if (content) {
    const txt = document.createElement("button");
    txt.className = "save";
    txt.textContent = "Save as text";
    txt.onclick = () => saveMessage(content, [], "txt");
    const pdf = document.createElement("button");
    pdf.className = "save";
    pdf.textContent = "Save as PDF";
    pdf.onclick = () => saveMessage(content, [], "pdf");
    actions.appendChild(txt);
    actions.appendChild(pdf);
  } else {
    return;
  }
  wrap.appendChild(actions);
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
    const id = state.streamId || state.conversationId;
    if (id) api(`/api/stop/${id}`, { method: "POST" });
    if (viewingStream()) stopThinking();
  };
  $("emptySettings").onclick = openSettings;
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
  if (state.streamId === id) api(`/api/stop/${id}`, { method: "POST" });
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
