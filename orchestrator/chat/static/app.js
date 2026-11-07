"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const terminal = new Set(["complete", "stopped", "error", "interrupted"]);
  const FILE_LIMIT = 10 * 1024 * 1024, MESSAGE_FILE_LIMIT = 20 * 1024 * 1024, MAX_FILES = 8;
  const imageTypes = new Set(["image/png", "image/jpeg", "image/webp"]);
  const state = { models: [], profiles: {}, conversations: [], conversation: null, selectedId: null, selectedModel: "", queue: {}, demo: false, mutation: false, settingsDirty: false, settingsModel: null, settingsRevision: null, stream: null, streamJob: null, pollBusy: false, requestSequence: 0, renderedConversation: null, searchTimer: null, editMessageId: null, editRevision: null, notice: "" };
  const attachmentDrafts = new Map(), attachmentPreviews = new Map();
  let editAttachments = [], dragDepth = 0;
  try { state.selectedId = sessionStorage.getItem("workbench-chat-conversation") || null; } catch (_) {}
  restoreAttachmentDrafts();

  class APIError extends Error {
    constructor(message, status, code) { super(message); this.status = status; this.code = code; }
  }

  async function request(path, body, signal) {
    const headers = { "Accept": "application/json" };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const response = await fetch(`/chat/api/${path}`, { method: body === undefined ? "GET" : "POST", headers, body: body === undefined ? undefined : JSON.stringify(body), credentials: "omit", cache: "no-store", redirect: "error", signal });
    let payload;
    try { payload = await response.json(); } catch (_) { throw new APIError("The server returned an unreadable response. Try again.", response.status, "invalid_response"); }
    if (!response.ok) {
      throw new APIError(payload.error?.message || "The request could not be completed.", response.status, payload.error?.code);
    }
    return payload;
  }

  function announce(text) { $("announcement").textContent = text; }
  function notice(message) { state.notice = message || ""; $("notice-text").textContent = state.notice; $("notice").hidden = !state.notice; }
  function errorMessage(error) { return error instanceof APIError ? error.message : "Could not reach the chat server. Check your connection and try again."; }
  function saveSelection(id) { state.selectedId = id; try { if (id) sessionStorage.setItem("workbench-chat-conversation", id); else sessionStorage.removeItem("workbench-chat-conversation"); } catch (_) { /* Selection can remain in memory. */ } }
  function stopStream() { if (state.stream) state.stream.abort(); state.stream = null; state.streamJob = null; }
  function clearAttachmentPreviews() { for (const loading of attachmentPreviews.values()) loading.then((url) => URL.revokeObjectURL(url)).catch(() => {}); attachmentPreviews.clear(); }
  function currentModel() { return state.models.find((model) => model.name === state.selectedModel); }
  function draftKey() { return state.selectedId || "new"; }
  function pendingFiles(key = draftKey()) { if (!attachmentDrafts.has(key)) attachmentDrafts.set(key, []); return attachmentDrafts.get(key); }
  function hasImageHistory() { return (state.conversation?.messages || []).some((message) => (message.attachments || []).some((attachment) => attachment.kind === "image")); }
  function needsVision() { return hasImageHistory() || pendingFiles().some((item) => item.attachment?.kind === "image" || imageTypes.has(item.file?.type)); }
  function supportsVision(model) { return Array.isArray(model?.capabilities) && model.capabilities.includes("vision"); }
  function activeJob(conversation = state.conversation) { const job = conversation?.active_job; return job && !terminal.has(job.status) ? job : null; }
  function modelSettings(modelName) {
    const model = state.models.find((item) => item.name === modelName);
    return state.profiles[modelName] || { ...model?.recommended_sampling, repetition_guard: true, system_prompt: model?.recommended_system_prompt || "" };
  }
  function uuid() {
    if (globalThis.crypto?.randomUUID) return crypto.randomUUID();
    if (!globalThis.crypto?.getRandomValues) throw new Error("This browser cannot create a secure request identifier.");
    const bytes = crypto.getRandomValues(new Uint8Array(16)); bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
    const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }

  function safeAttachment(value) {
    if (!value || !/^[a-f0-9]{32}$/.test(value.id) || !["text", "image"].includes(value.kind) || typeof value.name !== "string" || !Number.isFinite(value.size_bytes) || value.size_bytes < 0 || value.size_bytes > FILE_LIMIT) return null;
    const result = { id: value.id, name: value.name.slice(0, 255), kind: value.kind, media_type: String(value.media_type || "application/octet-stream"), size_bytes: value.size_bytes };
    for (const field of ["sha256", "created_at", "extracted_characters", "width", "height", "page_count"]) if (["string", "number"].includes(typeof value[field])) result[field] = value[field];
    if (typeof value.preview_text === "string") result.preview_text = value.preview_text.slice(0, 2000);
    if (Array.isArray(value.warnings)) result.warnings = value.warnings.filter((warning) => typeof warning === "string").slice(0, 10).map((warning) => warning.slice(0, 600));
    return result;
  }

  function restoreAttachmentDrafts() {
    try {
      const groups = JSON.parse(sessionStorage.getItem("workbench-chat-attachment-drafts") || "[]");
      if (!Array.isArray(groups)) return;
      for (const group of groups.slice(-30)) {
        if (!Array.isArray(group) || (group[0] !== "new" && !/^[a-f0-9]{32}$/.test(group[0])) || !Array.isArray(group[1])) continue;
        const key = group[0]; let total = 0;
        const entries = group[1].slice(0, MAX_FILES).map(safeAttachment).filter((attachment) => { if (!attachment || total + attachment.size_bytes > MESSAGE_FILE_LIMIT) return false; total += attachment.size_bytes; return true; }).map((attachment) => ({ localId: attachment.id, group: key, attachment, name: attachment.name, size: attachment.size_bytes, status: "ready" }));
        if (entries.length) attachmentDrafts.set(key, entries);
      }
    } catch (_) { /* Invalid or unavailable tab storage never blocks a conversation. */ }
  }

  function persistAttachmentDrafts() {
    const groups = [...attachmentDrafts.entries()].map(([key, entries]) => [key, entries.filter((item) => item.status === "ready").map((item) => safeAttachment(item.attachment)).filter(Boolean)]).filter((group) => group[1].length).slice(-30);
    try { if (groups.length) sessionStorage.setItem("workbench-chat-attachment-drafts", JSON.stringify(groups)); else sessionStorage.removeItem("workbench-chat-attachment-drafts"); } catch (_) { /* Files remain usable for this page when tab storage is full. */ }
  }

  function sizeLabel(size) { return size >= 1024 * 1024 ? `${(size / (1024 * 1024)).toFixed(1)} MB` : size >= 1024 ? `${Math.ceil(size / 1024)} KB` : `${size} B`; }
  function attachmentMeta(attachment) {
    const parts = [sizeLabel(attachment.size_bytes || 0)];
    if (attachment.kind === "image" && attachment.width && attachment.height) parts.push(`${attachment.width} × ${attachment.height}`);
    if (attachment.page_count) parts.push(`${attachment.page_count} page${attachment.page_count === 1 ? "" : "s"}`);
    if (attachment.extracted_characters !== undefined) parts.push(`${Number(attachment.extracted_characters).toLocaleString()} text characters`);
    return parts.join(" · ");
  }

  async function attachmentResponse(attachment, download = false) {
    const query = new URLSearchParams({ attachment_id: attachment.id }); if (download) query.set("download", "1");
    const response = await fetch(`/chat/api/attachment?${query}`, { credentials: "omit", cache: "no-store", redirect: "error" });
    if (!response.ok) {
      let error; try { error = await response.json(); } catch (_) { /* Fall back to a safe error message. */ }
      throw new APIError(error?.error?.message || "This attachment could not be loaded.", response.status, error?.error?.code);
    }
    return response;
  }

  async function imagePreview(attachment, image, placeholder) {
    try {
      if (!imageTypes.has(attachment.media_type)) throw new Error("Unsupported image preview");
      let loading = attachmentPreviews.get(attachment.id);
      if (!loading) {
        loading = (async () => { const response = await attachmentResponse(attachment); const type = response.headers.get("Content-Type")?.split(";", 1)[0]; if (!imageTypes.has(type)) throw new Error("Unexpected image preview"); const blob = await response.blob(); if (blob.size > FILE_LIMIT) throw new Error("Image preview exceeds limit"); return URL.createObjectURL(blob); })();
        attachmentPreviews.set(attachment.id, loading);
      }
      image.src = await loading; image.hidden = false; if (placeholder) placeholder.hidden = true;
    } catch (_) { if (placeholder) placeholder.textContent = "Preview unavailable"; }
  }

  async function downloadAttachment(attachment, button) {
    button.disabled = true;
    try { const response = await attachmentResponse(attachment, true); const blob = await response.blob(); const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = attachment.name; document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 60000); announce(`Download requested for ${attachment.name}.`); }
    catch (error) { notice(errorMessage(error)); }
    finally { button.disabled = false; }
  }

  function attachmentCard(attachment, { removable = false, remove = null, compact = false } = {}) {
    const card = document.createElement("div"); card.className = `attachment-card${compact ? " compact" : ""}`;
    const heading = document.createElement("div"); heading.className = "attachment-card-heading";
    const symbol = document.createElement("span"); symbol.className = "attachment-symbol"; symbol.setAttribute("aria-hidden", "true"); symbol.textContent = attachment.kind === "image" ? "▧" : "▤";
    const label = document.createElement("div"); label.className = "attachment-label"; const name = document.createElement("strong"); name.textContent = attachment.name; name.title = attachment.name; const meta = document.createElement("span"); meta.textContent = attachmentMeta(attachment); label.append(name, meta); heading.append(symbol, label);
    if (removable) { const button = document.createElement("button"); button.type = "button"; button.className = "attachment-remove"; button.textContent = "×"; button.setAttribute("aria-label", `Remove ${attachment.name}`); button.addEventListener("click", remove); heading.append(button); }
    else { const button = document.createElement("button"); button.type = "button"; button.className = "attachment-download"; button.textContent = "↓"; button.title = "Download original file"; button.setAttribute("aria-label", `Download ${attachment.name}`); button.addEventListener("click", () => downloadAttachment(attachment, button)); heading.append(button); }
    card.append(heading);
    if (attachment.kind === "image") { const image = document.createElement("img"); image.className = "attachment-image"; image.alt = attachment.name; image.hidden = true; image.loading = "lazy"; const placeholder = document.createElement("span"); placeholder.className = "image-placeholder"; placeholder.textContent = "Loading image preview…"; card.append(image, placeholder); imagePreview(attachment, image, placeholder); }
    else if (!compact && attachment.preview_text) { const details = document.createElement("details"); details.className = "attachment-text-preview"; const summary = document.createElement("summary"); summary.textContent = "Preview extracted text"; const preview = document.createElement("pre"); preview.textContent = attachment.preview_text; details.append(summary, preview); card.append(details); }
    for (const warning of attachment.warnings || []) { const line = document.createElement("p"); line.className = "attachment-warning"; line.textContent = warning; card.append(line); }
    return card;
  }

  function renderPendingAttachments() {
    const container = $("pending-attachments"); const entries = pendingFiles();
    const signature = JSON.stringify(entries.map((item) => [item.localId, item.status, item.progress, item.error, item.attachment]));
    container.hidden = !entries.length; if (container.dataset.signature === signature) return; container.dataset.signature = signature; container.replaceChildren();
    for (const item of entries) {
      if (item.status === "ready") { container.append(attachmentCard(item.attachment, { removable: true, compact: true, remove: () => removePendingFile(item) })); continue; }
      const card = document.createElement("div"); card.className = `upload-card${item.status === "error" ? " upload-error" : ""}`;
      const title = document.createElement("strong"); title.textContent = item.name;
      const status = document.createElement("span"); status.textContent = item.status === "error" ? item.error : item.status === "reading" ? `Reading file · ${item.progress || 0}%` : item.status === "waiting" ? "Waiting to upload…" : "Uploading and processing…"; if (item.status === "error") status.setAttribute("role", "alert");
      const remove = document.createElement("button"); remove.type = "button"; remove.className = "attachment-remove"; remove.textContent = "×"; remove.setAttribute("aria-label", `${item.status === "error" ? "Remove" : "Cancel upload of"} ${item.name}`); remove.addEventListener("click", () => removePendingFile(item)); card.append(title, status, remove);
      if (item.status === "error" && item.file) { const retry = document.createElement("button"); retry.type = "button"; retry.className = "upload-retry"; retry.textContent = "Retry upload"; retry.addEventListener("click", () => uploadFile(item)); card.append(retry); }
      container.append(card);
    }
  }

  function updateUploads() { persistAttachmentDrafts(); renderPendingAttachments(); renderModels(); renderControls(); }
  async function discardUnusedUpload(attachment) { try { await request("remove-upload", { attachment_id: attachment.id }); } catch (_) { /* A file already used by a sent message must remain on the server. */ } }
  function removePendingFile(item) {
    item.cancelled = true; if (item.reader?.readyState === FileReader.LOADING) item.reader.abort();
    const entries = pendingFiles(item.group), index = entries.indexOf(item); if (index >= 0) entries.splice(index, 1);
    if (item.attachment) discardUnusedUpload(item.attachment); updateUploads();
  }

  function discardAttachmentDraft(key) {
    const entries = attachmentDrafts.get(key) || []; attachmentDrafts.delete(key);
    for (const item of entries) { item.cancelled = true; if (item.reader?.readyState === FileReader.LOADING) item.reader.abort(); if (item.attachment) discardUnusedUpload(item.attachment); }
    updateUploads();
  }

  async function uploadFile(item) {
    if (item.cancelled || !item.file || ["reading", "uploading"].includes(item.status) || !pendingFiles(item.group).includes(item)) return;
    item.status = "reading"; item.progress = 0; item.error = ""; updateUploads();
    try {
      const base64 = await new Promise((resolve, reject) => { const reader = new FileReader(); item.reader = reader; reader.addEventListener("progress", (event) => { if (event.lengthComputable) { item.progress = Math.round(event.loaded * 100 / event.total); renderPendingAttachments(); } }); reader.addEventListener("load", () => resolve(String(reader.result).split(",", 2)[1])); reader.addEventListener("error", () => reject(new Error("The browser could not read this file."))); reader.addEventListener("abort", () => reject(new Error("Upload cancelled."))); reader.readAsDataURL(item.file); });
      if (item.cancelled) return;
      item.status = "uploading"; updateUploads();
      const payload = await request("upload", { name: item.name, content_base64: base64 }); const attachment = safeAttachment(payload.attachment); if (!attachment) throw new Error("The server returned invalid file details.");
      if (item.cancelled || !pendingFiles(item.group).includes(item)) { discardUnusedUpload(attachment); return; }
      item.attachment = attachment; item.status = "ready"; item.file = null; announce(`${item.name} is ready to send.`);
    } catch (error) { if (!item.cancelled) { item.status = "error"; item.error = error instanceof APIError ? error.message : error.message || "This file could not be uploaded."; } }
    finally { item.reader = null; updateUploads(); }
  }

  async function addFiles(files) {
    if (state.mutation) return;
    const entries = pendingFiles(); const key = draftKey(); const accepted = [];
    for (const file of Array.from(files)) {
      if (entries.length >= MAX_FILES) { notice("A message can have up to 8 files. Remove a file before adding another."); break; }
      if (file.size > FILE_LIMIT) { notice(`${file.name} exceeds the 10 MB per-file limit.`); continue; }
      if (entries.reduce((sum, item) => sum + item.size, 0) + file.size > MESSAGE_FILE_LIMIT) { notice("Files in one message must total 20 MB or less."); continue; }
      const item = { localId: uuid(), group: key, file, name: file.name || "pasted-image.png", size: file.size, status: "waiting", cancelled: false }; entries.push(item); accepted.push(item);
    }
    updateUploads();
    for (const item of accepted) await uploadFile(item);
  }

  function applyConversation(conversation, force = false) {
    if (!conversation) return;
    if (!force && state.conversation?.id === conversation.id && Number(conversation.revision) < Number(state.conversation.revision)) return;
    const changed = state.conversation?.id !== conversation.id;
    state.conversation = conversation; saveSelection(conversation.id);
    state.selectedModel = conversation.model || state.selectedModel;
    if (changed) { state.settingsDirty = false; state.renderedConversation = null; }
    render();
    const job = activeJob();
    if (job) attachStream(job.id); else stopStream();
  }

  async function refreshState() {
    if (state.pollBusy) return;
    state.pollBusy = true;
    const sequence = ++state.requestSequence;
    const requestedId = state.selectedId;
    const query = new URLSearchParams();
    if (requestedId) query.set("conversation_id", requestedId);
    if ($("conversation-search").value.trim()) query.set("q", $("conversation-search").value.trim());
    try {
      const payload = await request(`state${query.size ? `?${query}` : ""}`);
      if (sequence !== state.requestSequence) return;
      state.conversations = payload.conversations || []; state.profiles = payload.profiles || {}; state.queue = payload.queue || {}; state.demo = Boolean(payload.demo);
      if (payload.storage?.status === "error") notice(payload.storage.message || "Chat storage needs attention. Free disk space or delete an old conversation.");
      if (!query.has("q") && !state.mutation && requestedId === state.selectedId) {
        const savedIds = new Set(state.conversations.map((conversation) => conversation.id));
        for (const key of [...attachmentDrafts.keys()]) if (key !== "new" && !savedIds.has(key)) discardAttachmentDraft(key);
      }
      if (requestedId === state.selectedId && payload.conversation) applyConversation(payload.conversation);
      else render();
      if (requestedId && requestedId === state.selectedId && !payload.conversation && !state.mutation) { saveSelection(null); state.conversation = null; state.renderedConversation = null; stopStream(); discardAttachmentDraft(requestedId); render(); }
    } catch (error) { { if (error.status === 404 && requestedId === state.selectedId) { saveSelection(null); state.conversation = null; state.settingsDirty = false; state.renderedConversation = null; stopStream(); discardAttachmentDraft(requestedId); render(); notice("This conversation is no longer available. Select another conversation or start a new one."); } else notice(errorMessage(error)); } }
    finally { state.pollBusy = false; }
  }

  async function loadModels(refresh = false) {
    const button = $("refresh-models"); button.disabled = true;
    try {
      const payload = await request(`models${refresh ? "?refresh=1" : ""}`);
      state.models = payload.models || [];
      if (!state.selectedModel) state.selectedModel = state.models.find((model) => model.recommended_default && model.local && model.usable !== false && !model.metadata_error)?.name || state.models.find((model) => model.loaded && !model.experimental && model.local && model.usable !== false && !model.metadata_error)?.name || state.models.find((model) => !model.experimental && model.local && model.usable !== false && !model.metadata_error)?.name || "";
      render();
      if (!state.models.length) notice("No local chat models were found. Install a model on your PC, then refresh the model list.");
      else if (refresh) announce("Model list refreshed.");
    } catch (error) { notice(errorMessage(error)); }
    finally { button.disabled = false; }
  }

  async function enterApp() {
    notice("");
    await Promise.all([loadModels(), refreshState()]);
    $("composer-input").focus();
  }

  async function mutate(path, body, options = {}) {
    if (state.mutation) return null;
    state.mutation = true; renderControls();
    try {
      const payload = await request(path, body);
      if (path === "create" && payload.conversation && !state.selectedId) { const drafts = pendingFiles("new"); if (drafts.length) { for (const item of drafts) item.group = payload.conversation.id; attachmentDrafts.set(payload.conversation.id, drafts); attachmentDrafts.delete("new"); persistAttachmentDrafts(); } }
      if (payload.conversation && (path === "create" || payload.conversation.id === state.selectedId)) applyConversation(payload.conversation, true);
      notice("");
      return payload;
    } catch (error) {
      notice(error.status === 409 ? `${errorMessage(error)} Your message has been kept. Review the latest conversation and try again.` : errorMessage(error));
      if (error.status === 409) await refreshState();
      if (options.throwError) throw error;
      return null;
    } finally { state.mutation = false; renderControls(); refreshState(); }
  }

  async function ensureConversation(settings) {
    if (state.conversation) return state.conversation;
    if (!state.selectedModel) { notice("Choose an installed model before starting a conversation."); return null; }
    const result = await mutate("create", { model: state.selectedModel, settings: settings || modelSettings(state.selectedModel) });
    return result?.conversation || null;
  }

  function render() {
    renderSidebar(); renderModels(); renderMessages(); renderSettings(); renderPendingAttachments(); renderControls();
    const conversation = state.conversation;
    $("conversation-title").textContent = conversation?.title || "New conversation";
    $("conversation-subtitle").textContent = conversation ? state.demo ? "Saved in this local demo · Simulated models" : "Saved on this computer" : "";
    $("demo-badge").hidden = !state.demo;
    $("server-label").textContent = state.demo ? "Demo workspace" : "PC hosted";
    $("server-description").textContent = state.demo ? "Simulated responses · No PC connection" : "Conversations saved on your PC";
    document.querySelector(".server-identity").classList.toggle("demo", state.demo);
    document.title = `${conversation?.title || "New conversation"} · Local Chat`;
  }

  function renderSidebar() {
    const list = $("conversation-list");
    const previousRename = list.querySelector(".rename-input");
    if (previousRename && document.activeElement === previousRename) return;
    const signature = JSON.stringify([state.selectedId, $("conversation-search").value, state.conversations.map((conversation) => [conversation.id, conversation.title])]);
    if (!previousRename && list.dataset.signature === signature) return;
    list.dataset.signature = signature;
    list.replaceChildren();
    $("conversation-count").textContent = String(state.conversations.length);
    if (!state.conversations.length) { const empty = document.createElement("p"); empty.className = "empty-conversations"; empty.textContent = $("conversation-search").value ? "No matching conversations." : "Your conversations will find a home here."; list.append(empty); return; }
    for (const conversation of state.conversations) {
      const row = document.createElement("div"); row.className = `conversation-item${conversation.id === state.selectedId ? " selected" : ""}`;
      const open = document.createElement("button"); open.className = "conversation-open"; open.type = "button"; open.dataset.open = conversation.id; open.title = conversation.title || "Untitled conversation"; if (conversation.id === state.selectedId) open.setAttribute("aria-current", "true");
      const symbol = document.createElement("span"); symbol.className = "conversation-symbol"; symbol.setAttribute("aria-hidden", "true"); symbol.textContent = "◌";
      const name = document.createElement("span"); name.className = "conversation-name"; name.textContent = conversation.title || "Untitled conversation"; open.append(symbol, name);
      const menu = document.createElement("div"); menu.className = "conversation-menu";
      const rename = document.createElement("button"); rename.type = "button"; rename.textContent = "✎"; rename.dataset.rename = conversation.id; rename.title = "Rename conversation"; rename.setAttribute("aria-label", `Rename ${conversation.title || "conversation"}`);
      const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "×"; remove.dataset.delete = conversation.id; remove.title = "Delete conversation"; remove.setAttribute("aria-label", `Delete ${conversation.title || "conversation"}`);
      menu.append(rename, remove); row.append(open, menu); list.append(row);
    }
  }

  function renderModels() {
    const select = $("model-select"); const focused = document.activeElement === select;
    const visionRequired = needsVision();
    const signature = JSON.stringify([visionRequired, state.models.map((model) => [model.name, model.local, model.usable, model.metadata_error, model.capabilities])]);
    if (!focused && select.dataset.catalog !== signature) {
      select.replaceChildren();
      if (!state.models.length) { const option = document.createElement("option"); option.value = ""; option.textContent = "No models available"; select.append(option); }
      for (const model of state.models) { const option = document.createElement("option"); option.value = model.name; const unavailable = model.local === false || model.usable === false || Boolean(model.metadata_error); const incompatible = visionRequired && !supportsVision(model); option.textContent = `${model.name}${model.experimental ? " · experimental coding" : model.profile === "coding" ? " · coding" : ""}${unavailable ? " · unavailable" : incompatible ? " · vision required" : supportsVision(model) ? " · vision" : ""}`; option.disabled = unavailable || incompatible; select.append(option); }
      select.dataset.catalog = signature;
    }
    if (state.selectedModel && !Array.from(select.options).some((option) => option.value === state.selectedModel)) { const option = document.createElement("option"); option.value = state.selectedModel; option.textContent = `${state.selectedModel} · unavailable`; option.disabled = true; select.append(option); }
    select.value = state.selectedModel;
    $("loaded-badge").hidden = !currentModel()?.loaded;
  }

  function renderControls() {
    const job = activeJob(); const model = currentModel();
    const files = pendingFiles(), busyFiles = files.some((item) => item.status !== "ready");
    $("send-button").hidden = Boolean(job); $("stop-button").hidden = !job;
    $("send-button").disabled = state.mutation || busyFiles || !state.selectedModel || !model || model.local === false || model.usable === false || Boolean(model.metadata_error) || (needsVision() && !supportsVision(model)) || (!$("composer-input").value.trim() && !files.length);
    $("attach-button").disabled = state.mutation || files.length >= MAX_FILES;
    const attachmentMessage = needsVision() && !supportsVision(model) ? "Images require a model with vision support. Choose a vision model to continue." : busyFiles ? files.some((item) => item.status === "error") ? "Remove or retry files that could not be uploaded before sending." : "Wait for your files to finish uploading before sending." : "";
    $("attachment-message").textContent = attachmentMessage; $("attachment-message").hidden = !attachmentMessage;
    $("stop-button").disabled = state.mutation;
    $("model-select").disabled = Boolean(job) || state.mutation;
    $("save-settings").disabled = Boolean(job) || state.mutation || !state.selectedModel || (state.settingsDirty && state.settingsModel !== state.selectedModel);
    $("reset-settings").hidden = !state.settingsDirty;
    $("generation-status").hidden = !job;
    if (job) {
      const position = Number(job.queue_position);
      const labels = { queued: Number.isFinite(position) && position > 0 ? `Queue position ${position} · Waiting for the model` : "Queued · Waiting for the model", loading: `Loading ${job.model || state.selectedModel} on your PC…`, thinking: "Thinking through your question…", generating: "Writing a response…", stopping: "Stopping the response…" };
      $("generation-status").textContent = job.checkpoint_pending ? (job.error?.message || "Waiting to save this response. Check free disk space.") : labels[job.status] || "Waiting for the model…";
    }
    for (const button of $("messages").querySelectorAll("button[data-edit],button[data-regenerate]")) button.disabled = Boolean(job) || state.mutation;
  }

  function renderSettings() {
    const model = currentModel(); const name = state.selectedModel;
    if (state.settingsDirty) { if (state.settingsModel !== name) $("settings-feedback").textContent = "The conversation's model changed. Discard these pending settings before editing the new model."; return; }
    $("settings-model-name").textContent = name || "Choose a model";
    const capabilities = Array.isArray(model?.capabilities) ? model.capabilities.join(" · ") : "";
    $("model-capabilities").textContent = model?.metadata_error ? "Model details are unavailable. Standard runtime controls are shown; thinking options are hidden." : capabilities ? `Capabilities: ${capabilities}` : "Standard runtime controls · Model details unavailable";
    state.settingsModel = name;
    const settings = state.conversation?.model === name ? state.conversation.settings || modelSettings(name) : modelSettings(name);
    const contextLimit = Number(model?.context_length);
    const contextCeiling = Number.isFinite(contextLimit) && contextLimit > 0 ? Math.min(131072, contextLimit) : 8192;
    $("temperature").value = settings.temperature ?? 1.0; $("num-ctx").value = settings.num_ctx ?? Math.min(model?.recommended_context || 16384, contextCeiling); $("num-predict").value = settings.num_predict ?? 2048; $("system-prompt").value = settings.system_prompt || "";
    $("top-p").value = settings.top_p ?? "";
    $("top-k").value = settings.top_k ?? "";
    $("repetition-guard").checked = settings.repetition_guard !== false;
    $("num-ctx").max = String(contextCeiling);
    $("context-help").textContent = Number.isFinite(contextLimit) && contextLimit > 0 ? `Model limit: ${contextLimit.toLocaleString()} tokens. Chat allows up to ${contextCeiling.toLocaleString()}. Larger windows use more memory.` : "Model limit unavailable. Chat allows up to 8,192 tokens. Larger windows use more memory.";
    const values = Array.isArray(model?.thinking?.values) ? model.thinking.values.filter((value) => typeof value === "boolean" || typeof value === "string") : [];
    $("thinking-field").hidden = !values.length;
    const think = $("thinking-select"); think.replaceChildren(); const defaultOption = document.createElement("option"); defaultOption.value = "null"; defaultOption.textContent = "Model default"; think.append(defaultOption);
    for (const value of values) { const option = document.createElement("option"); option.value = JSON.stringify(value); option.textContent = typeof value === "boolean" ? value ? "On" : "Off" : value.charAt(0).toUpperCase() + value.slice(1); think.append(option); }
    const chosenThinking = Object.hasOwn(settings, "think") ? settings.think : model?.recommended_thinking;
    think.value = values.some((value) => value === chosenThinking) ? JSON.stringify(chosenThinking) : "null";
  }

  function readSettings() {
    const numeric = (id) => $(id).value.trim() === "" ? null : Number($(id).value);
    return { temperature: numeric("temperature"), top_p: numeric("top-p"), top_k: numeric("top-k"), repetition_guard: $("repetition-guard").checked, num_ctx: numeric("num-ctx"), num_predict: numeric("num-predict"), think: $("thinking-field").hidden ? null : JSON.parse($("thinking-select").value), system_prompt: $("system-prompt").value };
  }

  function addInline(parent, text) {
    // Every match becomes a DOM text node or a small, fixed set of safe elements.
    const pattern = /(`[^`\n]+`|\*\*[^*\n]+\*\*)/g; let offset = 0; let match;
    while ((match = pattern.exec(text))) { parent.append(document.createTextNode(text.slice(offset, match.index))); const node = document.createElement(match[0].startsWith("`") ? "code" : "strong"); node.textContent = match[0].startsWith("`") ? match[0].slice(1, -1) : match[0].slice(2, -2); parent.append(node); offset = pattern.lastIndex; }
    parent.append(document.createTextNode(text.slice(offset)));
  }

  function markdown(text) {
    const fragment = document.createDocumentFragment(); const lines = String(text || "").split("\n"); let paragraph = []; let list = null;
    const flush = () => { if (paragraph.length) { const p = document.createElement("p"); addInline(p, paragraph.join("\n")); fragment.append(p); paragraph = []; } list = null; };
    for (let index = 0; index < lines.length; index++) {
      const line = lines[index]; const fence = line.match(/^\s*```([^`]*)$/);
      if (fence) {
        flush(); const codeLines = []; while (++index < lines.length && !/^\s*```\s*$/.test(lines[index])) codeLines.push(lines[index]);
        const block = document.createElement("div"); block.className = "code-block"; const top = document.createElement("div"); top.className = "code-top"; const language = document.createElement("span"); language.textContent = fence[1].trim() || "code"; const copy = document.createElement("button"); copy.type = "button"; copy.textContent = "Copy code"; copy.addEventListener("click", () => copyText(codeLines.join("\n"), copy)); top.append(language, copy); const pre = document.createElement("pre"); const code = document.createElement("code"); code.textContent = codeLines.join("\n"); pre.append(code); block.append(top, pre); fragment.append(block); continue;
      }
      const heading = line.match(/^(#{1,4})\s+(.+)$/); const item = line.match(/^\s*(?:([-*+])\s+|(\d+)\.\s+)(.*)$/);
      if (heading) { flush(); const h = document.createElement(`h${heading[1].length}`); addInline(h, heading[2]); fragment.append(h); }
      else if (item) { if (paragraph.length) flush(); const type = item[2] ? "ol" : "ul"; if (!list || list.tagName.toLowerCase() !== type) { list = document.createElement(type); fragment.append(list); } const li = document.createElement("li"); addInline(li, item[3]); list.append(li); }
      else if (!line.trim()) flush();
      else { list = null; paragraph.push(line); }
    }
    flush(); return fragment;
  }

  function metricText(message) {
    const metrics = message.metrics || {}; const parts = [];
    const tokens = Number(metrics.eval_count ?? metrics.output_tokens ?? metrics.completion_tokens);
    if (Number.isFinite(tokens) && tokens > 0) parts.push(`${tokens.toLocaleString()} tokens`);
    const rate = Number(metrics.tokens_per_second ?? (metrics.eval_duration > 0 && tokens > 0 ? tokens / (metrics.eval_duration / 1e9) : NaN));
    if (Number.isFinite(rate) && rate > 0) parts.push(`${rate.toFixed(1)} tok/s`);
    const duration = Number(metrics.total_duration_ns ?? metrics.total_duration); if (Number.isFinite(duration) && duration > 0) parts.push(`${(duration / 1e9).toFixed(1)}s`); else if (Number(metrics.elapsed_seconds) > 0) parts.push(`${Number(metrics.elapsed_seconds).toFixed(1)}s`);
    return parts.join(" · ");
  }

  function messageNode(message, lastAssistantId) {
    const article = document.createElement("article"); article.className = `message ${message.role === "user" ? "user" : "assistant"}`; article.dataset.messageId = message.id;
    const header = document.createElement("div"); header.className = "message-header"; const avatar = document.createElement("span"); avatar.className = "message-avatar"; avatar.setAttribute("aria-hidden", "true"); avatar.textContent = message.role === "user" ? "↗" : "✦"; const author = document.createElement("span"); author.className = "message-author"; author.textContent = message.role === "user" ? "You" : "Local Chat"; header.append(avatar, author);
    if (message.role !== "user" && message.model) { const model = document.createElement("span"); model.className = "message-model"; model.textContent = message.model; header.append(model); } article.append(header);
    if (message.thinking) { const details = document.createElement("details"); details.className = "thinking"; const summary = document.createElement("summary"); summary.textContent = message.status === "thinking" ? "Thinking…" : "Thinking"; const content = document.createElement("div"); content.className = "thinking-content"; content.textContent = message.thinking; details.append(summary, content); article.append(details); }
    const content = document.createElement("div"); content.className = "message-content";
    if (message.role === "user") { const p = document.createElement("p"); p.textContent = message.content || ""; content.append(p); }
    else if (message.content) content.append(markdown(message.content));
    else if (!terminal.has(message.status) && message.status !== "thinking") { const dot = document.createElement("span"); dot.className = "typing-dot"; dot.setAttribute("aria-label", "Waiting for response"); content.append(dot); }
    article.append(content);
    if (Array.isArray(message.attachments) && message.attachments.length) { const files = document.createElement("div"); files.className = "message-attachments"; files.setAttribute("aria-label", "Message attachments"); for (const attachment of message.attachments.map(safeAttachment).filter(Boolean)) files.append(attachmentCard(attachment)); article.append(files); }
    if (["error", "interrupted", "stopped"].includes(message.status)) { const status = document.createElement("p"); status.className = `message-state${message.status === "error" ? " error" : ""}`; status.textContent = message.status === "error" ? "Response failed. Regenerate to try again." : message.status === "interrupted" ? "Response interrupted by a server restart. Regenerate to continue." : "Response stopped."; article.append(status); }
    const actions = document.createElement("div"); actions.className = "message-actions";
    if (message.content) { const copy = document.createElement("button"); copy.type = "button"; copy.textContent = "Copy"; copy.addEventListener("click", () => copyText(message.content, copy)); actions.append(copy); }
    if (message.role === "user") { const edit = document.createElement("button"); edit.type = "button"; edit.textContent = "Edit"; edit.dataset.edit = message.id; edit.addEventListener("click", () => editMessage(message)); actions.append(edit); }
    else if (message.id === lastAssistantId && terminal.has(message.status || "complete")) { const regenerate = document.createElement("button"); regenerate.type = "button"; regenerate.textContent = message.status === "error" ? "Retry response" : "Regenerate"; regenerate.dataset.regenerate = message.id; regenerate.addEventListener("click", regenerateResponse); actions.append(regenerate); }
    const metrics = metricText(message); if (metrics) { const label = document.createElement("span"); label.className = "message-metrics"; label.textContent = metrics; actions.append(label); } article.append(actions); return article;
  }

  function renderMessages() {
    const scroll = $("message-scroll"); const messages = state.conversation?.messages || []; const container = $("messages"); const changed = state.renderedConversation !== state.conversation?.id; const nearBottom = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 130;
    if (changed) { container.replaceChildren(); state.renderedConversation = state.conversation?.id || null; }
    $("welcome").hidden = messages.length > 0; $("messages").hidden = messages.length === 0;
    const lastAssistantId = [...messages].reverse().find((message) => message.role === "assistant")?.id;
    const remaining = new Set(messages.map((message) => message.id));
    for (const child of [...container.children]) if (!remaining.has(child.dataset.messageId)) child.remove();
    const existing = new Map([...container.children].map((node) => [node.dataset.messageId, node]));
    for (let index = 0; index < messages.length; index++) {
      const message = messages[index]; const signature = JSON.stringify([message, lastAssistantId]); const previous = existing.get(message.id);
      let node = previous;
      if (!previous || previous.dataset.signature !== signature) { node = messageNode(message, lastAssistantId); node.dataset.signature = signature; const oldDetails = previous?.querySelector("details"); if (oldDetails?.open && node.querySelector("details")) node.querySelector("details").open = true; if (previous) previous.replaceWith(node); }
      if (container.children[index] !== node) container.insertBefore(node, container.children[index] || null);
    }
    if (changed || nearBottom) scroll.scrollTop = scroll.scrollHeight;
    const job = activeJob(); if (job?.error) notice(typeof job.error === "string" ? job.error : job.error.message || "The response could not be completed.");
  }

  async function copyText(value, button) {
    try {
      let copied = false;
      if (navigator.clipboard?.writeText) { try { await navigator.clipboard.writeText(value); copied = true; } catch (_) { /* HTTP LAN pages may need the selection fallback. */ } }
      if (!copied) { const input = document.createElement("textarea"); input.value = value; input.style.position = "fixed"; input.style.opacity = "0"; document.body.append(input); input.select(); copied = document.execCommand("copy"); input.remove(); if (!copied) throw new Error("Copy unavailable"); }
      if (button) { const original = button.textContent; button.textContent = "Copied"; setTimeout(() => { if (button.isConnected) button.textContent = original; }, 1600); } announce("Copied to clipboard.");
    } catch (_) { notice("Copy is unavailable in this browser. Select the text and copy it manually."); }
  }

  async function attachStream(jobId) {
    if (!jobId || state.streamJob === jobId) return;
    stopStream(); const controller = new AbortController(); state.stream = controller; state.streamJob = jobId;
    try {
      const response = await fetch(`/chat/api/stream?${new URLSearchParams({ job_id: jobId })}`, { headers: { "Accept": "application/x-ndjson" }, credentials: "omit", cache: "no-store", redirect: "error", signal: controller.signal });
      if (!response.ok) { throw new Error("Stream unavailable"); }
      if (!response.body) throw new Error("Streaming unavailable");
      const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = "";
      const consume = (line) => {
        if (!line.trim()) return; const update = JSON.parse(line); if (update.heartbeat || controller.signal.aborted) return;
        if (update.job?.conversation_id !== state.selectedId) return;
        if (update.conversation) { applyConversation(update.conversation); }
        else if (update.message && state.conversation && Number(update.revision) >= Number(state.conversation.revision)) {
          const messages = state.conversation.messages; const index = messages.findIndex((message) => message.id === update.message.id); if (index >= 0) messages[index] = update.message; else messages.push(update.message);
          state.conversation.active_job = update.job; state.conversation.revision = update.revision; renderMessages(); renderControls();
        }
        if (update.job && terminal.has(update.job.status)) { announce(update.job.status === "complete" ? "Response complete." : `Response ${update.job.status}.`); if (update.job.error) notice(typeof update.job.error === "string" ? update.job.error : update.job.error.message || "The model response failed."); refreshState(); }
      };
      while (!controller.signal.aborted) { const { value, done } = await reader.read(); buffer += decoder.decode(value || new Uint8Array(), { stream: !done }); let newline; while ((newline = buffer.indexOf("\n")) >= 0) { const line = buffer.slice(0, newline); buffer = buffer.slice(newline + 1); consume(line); } if (done) { if (buffer.trim()) consume(buffer); break; } }
    } catch (error) { if (error.name !== "AbortError") { /* Polling restores the stream without cancelling generation. */ } }
    finally { if (state.stream === controller) { state.stream = null; state.streamJob = null; } }
  }

  function closeSidebar() { $("sidebar").classList.remove("open"); $("sidebar-scrim").hidden = true; }
  function toggleSettings(open) { $("settings-panel").hidden = !open; $("settings-toggle").setAttribute("aria-expanded", String(open)); if (open) $("settings-close").focus(); }
  async function selectConversation(id) {
    if (state.mutation) return;
    stopStream(); state.requestSequence++; saveSelection(id); state.conversation = null; state.settingsDirty = false; state.renderedConversation = null; closeSidebar(); render(); await refreshState();
  }

  async function newConversation() {
    stopStream(); state.requestSequence++; saveSelection(null); state.conversation = null; state.settingsDirty = false; state.renderedConversation = null; $("composer-input").value = ""; resizeComposer(); closeSidebar(); notice(""); render(); $("composer-input").focus();
  }

  function renderEditAttachments() { const container = $("edit-attachments"); container.hidden = !editAttachments.length; container.replaceChildren(); for (const attachment of editAttachments) container.append(attachmentCard(attachment, { removable: true, compact: true, remove: () => { editAttachments = editAttachments.filter((item) => item.id !== attachment.id); renderEditAttachments(); } })); }
  function editMessage(message) { if (activeJob() || state.mutation) return; state.editMessageId = message.id; state.editRevision = state.conversation.revision; editAttachments = (message.attachments || []).map(safeAttachment).filter(Boolean); renderEditAttachments(); $("edit-input").value = message.content || ""; $("edit-error").textContent = ""; $("edit-dialog").showModal(); $("edit-input").focus(); }
  async function regenerateResponse() { if (!state.conversation || activeJob()) return; await mutate("regenerate", { conversation_id: state.conversation.id, revision: state.conversation.revision, request_id: uuid() }); }
  function resizeComposer() { const input = $("composer-input"); input.style.height = "auto"; input.style.height = `${Math.min(input.scrollHeight, 210)}px`; renderControls(); }

  $("new-chat").addEventListener("click", newConversation);
  $("conversation-search").addEventListener("input", () => { clearTimeout(state.searchTimer); state.searchTimer = setTimeout(refreshState, 220); });
  $("conversation-list").addEventListener("click", async (event) => {
    const button = event.target.closest("button"); if (!button) return;
    if (button.dataset.open) { await selectConversation(button.dataset.open); return; }
    const id = button.dataset.rename || button.dataset.delete; const summary = state.conversations.find((conversation) => conversation.id === id); if (!summary || state.mutation) return;
    if (button.dataset.rename) {
      const row = button.closest(".conversation-item"); const name = row.querySelector(".conversation-name"); const input = document.createElement("input"); input.className = "rename-input"; input.value = summary.title || ""; input.maxLength = 120; input.setAttribute("aria-label", "Conversation title"); name.replaceWith(input); input.focus(); input.select(); let submitted = false;
      const save = async () => { if (submitted) return; submitted = true; const title = input.value.trim(); input.blur(); if (title && title !== summary.title) await mutate("update", { conversation_id: id, title, revision: summary.revision }); renderSidebar(); };
      input.addEventListener("click", (click) => click.stopPropagation()); input.addEventListener("keydown", (key) => { if (key.key === "Enter") { key.preventDefault(); save(); } if (key.key === "Escape") { submitted = true; input.blur(); renderSidebar(); } }); input.addEventListener("blur", save, { once: true });
    } else {
      $("confirm-description").textContent = `“${summary.title || "Untitled conversation"}” and its messages will be removed from your PC.`; const dialog = $("confirm-dialog"); dialog.returnValue = ""; dialog.showModal(); dialog.addEventListener("close", async () => { if (dialog.returnValue !== "confirm") return; const result = await mutate("delete", { conversation_id: id, revision: summary.revision }); if (result) discardAttachmentDraft(id); if (result && state.selectedId === id) await newConversation(); }, { once: true });
    }
  });
  $("model-select").addEventListener("change", async () => {
    const previous = state.selectedModel; const selected = $("model-select").value; state.selectedModel = selected; state.settingsDirty = false; $("settings-feedback").textContent = "";
    if (state.conversation) { const result = await mutate("update", { conversation_id: state.conversation.id, model: selected, settings: modelSettings(selected), revision: state.conversation.revision }); if (!result) state.selectedModel = state.conversation?.model || previous; }
    render();
  });
  $("refresh-models").addEventListener("click", () => loadModels(true));
  $("settings-toggle").addEventListener("click", () => toggleSettings($("settings-panel").hidden)); $("settings-close").addEventListener("click", () => toggleSettings(false));
  function markSettingsDirty() { if (!state.settingsDirty) state.settingsRevision = state.conversation?.revision ?? null; state.settingsDirty = true; $("settings-feedback").textContent = "Unsaved changes"; renderControls(); }
  $("settings-form").addEventListener("input", markSettingsDirty);
  $("settings-form").addEventListener("change", markSettingsDirty);
  $("reset-settings").addEventListener("click", () => { state.settingsDirty = false; state.settingsRevision = null; $("settings-feedback").textContent = ""; renderSettings(); renderControls(); });
  $("settings-form").addEventListener("submit", async (event) => {
    event.preventDefault(); const settings = readSettings(); const conversation = await ensureConversation(settings); if (!conversation) return;
    const result = await mutate("update", { conversation_id: conversation.id, settings, revision: state.settingsRevision ?? state.conversation.revision }); if (result) { state.settingsDirty = false; state.settingsRevision = null; state.profiles[state.selectedModel] = settings; $("settings-feedback").textContent = "Settings saved for this model."; renderSettings(); renderControls(); announce("Model settings saved."); }
  });
  $("composer-input").addEventListener("input", resizeComposer);
  $("composer-input").addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); if (!$("send-button").disabled && !activeJob()) $("composer-form").requestSubmit(); } });
  $("composer-form").addEventListener("submit", async (event) => {
    event.preventDefault(); const content = $("composer-input").value.trim(); const files = pendingFiles(); if ((!content && !files.length) || files.some((item) => item.status !== "ready") || activeJob() || state.mutation) return;
    if (needsVision() && !supportsVision(currentModel())) { notice("Select a model whose reported capabilities include vision before sending images."); return; }
    if (state.settingsDirty) { notice("Save or discard your pending model settings before sending."); toggleSettings(true); return; }
    const conversation = await ensureConversation(); if (!conversation) return;
    const attachmentIds = files.map((item) => item.attachment.id);
    const result = await mutate("send", { conversation_id: conversation.id, content, attachment_ids: attachmentIds, revision: state.conversation?.id === conversation.id ? state.conversation.revision : conversation.revision, request_id: uuid() });
    if (result) { attachmentDrafts.set(conversation.id, pendingFiles(conversation.id).filter((item) => !attachmentIds.includes(item.attachment?.id))); persistAttachmentDrafts(); renderPendingAttachments(); renderModels(); if ($("composer-input").value.trim() === content) { $("composer-input").value = ""; resizeComposer(); $("composer-input").focus(); } renderControls(); }
  });
  $("stop-button").addEventListener("click", async () => { const job = activeJob(); if (job) await mutate("stop", { job_id: job.id }); });
  $("edit-form").addEventListener("submit", async (event) => { event.preventDefault(); const content = $("edit-input").value.trim(); if ((!content && !editAttachments.length) || !state.conversation || activeJob()) { if (!content && !editAttachments.length) $("edit-error").textContent = "Keep a message or at least one attachment."; return; } $("edit-submit").disabled = true; try { const result = await mutate("edit", { conversation_id: state.conversation.id, message_id: state.editMessageId, content, attachment_ids: editAttachments.map((attachment) => attachment.id), revision: state.editRevision, request_id: uuid() }); if (result) $("edit-dialog").close(); else $("edit-error").textContent = state.notice; } finally { $("edit-submit").disabled = false; } });
  $("edit-close").addEventListener("click", () => $("edit-dialog").close()); $("edit-cancel").addEventListener("click", () => $("edit-dialog").close());
  $("dismiss-notice").addEventListener("click", () => notice(""));
  $("open-sidebar").addEventListener("click", () => { $("sidebar").classList.add("open"); $("sidebar-scrim").hidden = false; $("new-chat").focus(); }); $("close-sidebar").addEventListener("click", closeSidebar); $("sidebar-scrim").addEventListener("click", closeSidebar);
  for (const button of document.querySelectorAll(".starter")) button.addEventListener("click", () => { $("composer-input").value = button.dataset.prompt; resizeComposer(); $("composer-input").focus(); });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") { closeSidebar(); if (!$("settings-panel").hidden) toggleSettings(false); } });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshState(); });
  $("attach-button").addEventListener("click", () => $("file-input").click());
  $("file-input").addEventListener("change", (event) => { addFiles(event.target.files); event.target.value = ""; });
  $("composer-input").addEventListener("paste", (event) => { const files = Array.from(event.clipboardData?.items || []).filter((item) => item.kind === "file" && item.type.startsWith("image/")).map((item) => item.getAsFile()).filter(Boolean); if (!files.length) return; event.preventDefault(); const text = event.clipboardData.getData("text/plain"); if (text) { const input = $("composer-input"); input.setRangeText(text, input.selectionStart, input.selectionEnd, "end"); resizeComposer(); } addFiles(files); });
  const composer = $("composer-form");
  composer.addEventListener("dragenter", (event) => { if (!Array.from(event.dataTransfer?.types || []).includes("Files")) return; event.preventDefault(); dragDepth++; $("drop-hint").hidden = false; composer.classList.add("dragging"); });
  composer.addEventListener("dragover", (event) => { if (Array.from(event.dataTransfer?.types || []).includes("Files")) { event.preventDefault(); event.dataTransfer.dropEffect = "copy"; } });
  composer.addEventListener("dragleave", (event) => { event.preventDefault(); dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) { $("drop-hint").hidden = true; composer.classList.remove("dragging"); } });
  composer.addEventListener("drop", (event) => { if (!event.dataTransfer?.files.length) return; event.preventDefault(); dragDepth = 0; $("drop-hint").hidden = true; composer.classList.remove("dragging"); addFiles(event.dataTransfer.files); });
  window.addEventListener("beforeunload", clearAttachmentPreviews);
  setInterval(() => { if (!document.hidden && !state.mutation) refreshState(); }, 2000);
  enterApp();
})();
