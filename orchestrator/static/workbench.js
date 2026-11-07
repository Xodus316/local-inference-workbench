"use strict";
(() => {
  const el = (name) => document.getElementById(name);
  let selected = null;
  let revision = 0;
  let busy = false;
  let polling = false;
  let testNames = [];

  async function api(path, body) {
    const options = { method: body === undefined ? "GET" : "POST", cache: "no-store",
                      redirect: "error", credentials: "omit" };
    if (body !== undefined) {
      options.headers = {"Content-Type": "application/json"};
      options.body = JSON.stringify(body);
    }
    const response = await fetch(path, options);
    const value = await response.json();
    if (!response.ok) throw new Error(value.error?.message || (typeof value.error === "string" ? value.error : "Request failed"));
    return value;
  }
  function message(text) { el("notice").textContent = text; }
  function renderRun(row) {
    const previous = selected?.id;
    selected = row;
    revision++;
    el("selected").hidden = false;
    el("status").textContent = row.status.replaceAll("_", " ");
    const info = el("metadata");
    info.replaceChildren();
    const request = row.bundle.request;
    for (const [name, value] of [["Run", row.id], ["Mode", request.mode], ["Request", request.prompt]]) {
      const dt = document.createElement("dt");
      const dd = document.createElement("dd");
      dt.textContent = name; dd.textContent = value; info.append(dt, dd);
    }
    el("input").textContent = JSON.stringify(row.bundle, null, 2);
    if (previous !== row.id) el("input-view").open = row.status === "prepared";
    el("calls").replaceChildren();
    for (const call of row.calls || []) {
      const item = document.createElement("div"); item.className = "call";
      item.textContent = [call.stage, call.model, call.status].filter(Boolean).join(" · ");
      el("calls").append(item);
    }
    const result = row.result || {};
    const output = result.output ?? result.review ?? result;
    el("result-block").hidden = !row.error && !row.result;
    el("result").textContent = row.error || (typeof output === "string" ? output : JSON.stringify(output, null, 2));
    el("diff-block").hidden = !result.diff;
    el("diff").textContent = result.diff || "";
    el("review-block").hidden = !result.review;
    el("review").textContent = result.review ? JSON.stringify(result.review, null, 2) : "";
    el("workspace-block").hidden = !result.worktree;
    el("workspace").textContent = result.worktree ? result.worktree + "\n" + JSON.stringify(result.tests || [], null, 2) : "";
    el("resume").hidden = row.status !== "interrupted";
    el("approve").hidden = row.status !== "prepared";
    el("apply").hidden = row.status !== "awaiting_apply" || !result.apply_digest;
    el("cancel").hidden = !["queued", "running", "prepared"].includes(row.status);
    for (const button of el("runs").querySelectorAll("button")) button.setAttribute("aria-pressed", String(button.dataset.id === row.id));
  }
  async function select(id) {
    if (busy) return;
    const seq = ++revision;
    try {
      const row = await api("/api/runs/" + encodeURIComponent(id));
      if (seq === revision && !busy) { renderRun(row); message(""); }
    } catch (error) { message(error.message); }
  }
  function list(rows) {
    const container = el("runs"); container.replaceChildren();
    for (const row of rows) {
      const li = document.createElement("li"); const button = document.createElement("button");
      const small = document.createElement("small");
      button.type = "button"; button.dataset.id = row.id;
      button.setAttribute("aria-pressed", String(row.id === selected?.id));
      button.textContent = row.id.slice(0, 8);
      small.textContent = [row.status, new Date(row.created * 1000).toLocaleString()].join(" · ");
      button.append(small); button.onclick = () => select(row.id); li.append(button); container.append(li);
    }
    if (!rows.length) { const note = document.createElement("li"); note.textContent = "No saved runs."; container.append(note); }
  }
  async function refresh() {
    if (busy || polling) return;
    polling = true;
    const seq = revision;
    try {
      const current = await api("/api/runs");
      if (!busy && seq === revision) list(current.runs || []);
      if (selected && !busy && seq === revision) {
        const row = await api("/api/runs/" + encodeURIComponent(selected.id));
        if (!busy && seq === revision) renderRun(row);
      }
    } catch (error) { message(error.message); }
    finally { polling = false; }
  }
  async function mutation(path, body) {
    if (busy) return;
    revision++; busy = true; message("");
    const buttons = ["prepare-button", "approve", "resume", "apply", "cancel"];
    for (const id of buttons) el(id).disabled = true;
    try { renderRun(await api(path, body)); }
    catch (error) { message(error.message + ". Check Saved runs for the latest state."); }
    finally { busy = false; for (const id of buttons) el(id).disabled = false; await refresh(); }
  }
  el("mode").onchange = () => {
    const repo = ["edit", "plan"].includes(el("mode").value);
    el("repository-fields").hidden = !repo;
    el("repository").required = repo; el("files").required = repo;
    el("test-field").hidden = el("mode").value !== "edit" || !testNames.length;
    el("research-field").hidden = el("mode").value !== "balanced";
  };
  el("mode").onchange();
  el("prepare").onsubmit = (event) => {
    event.preventDefault();
    const mode = el("mode").value;
    const body = {mode, prompt: el("prompt").value, sources: []};
    if (el("source").value.trim()) body.sources.push({id: "source", text: el("source").value});
    if (mode === "balanced") body.research = el("research").checked;
    if (mode === "plan" || mode === "edit") {
      body.repository = el("repository").value.trim();
      body.files = el("files").value.split(/\r?\n/).map(x => x.trim()).filter(Boolean);
    }
    if (mode === "edit") {
      body.tests = Array.from(el("test-options").querySelectorAll("input:checked"), item => item.value);
    }
    mutation("/api/runs", body);
  };
  for (const action of ["approve", "resume", "cancel", "apply"]) el(action).onclick = () => {
    if (!selected || busy || el(action).hidden) return;
    let body = {};
    if (action === "approve" || action === "resume") body = {digest: selected.digest};
    if (action === "apply") body = {digest: selected.result.apply_digest};
    mutation("/api/runs/" + encodeURIComponent(selected.id) + "/" + action, body);
  };
  async function loadTests() {
    try {
      const status = await api("/api/status");
      testNames = status.test_commands || [];
      for (const name of testNames) {
        const label = document.createElement("label"); label.className = "test-name";
        const checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.value = name;
        label.append(checkbox, document.createTextNode(name)); el("test-options").append(label);
      }
      el("mode").onchange();
    } catch (error) { message(error.message); }
  }
  const linked = new URLSearchParams(location.hash.slice(1)).get("run");
  if (linked && /^[0-9a-f-]{36}$/.test(linked)) select(linked);
  loadTests();
  el("refresh").onclick = refresh;
  refresh();
  setInterval(() => { if (!document.hidden) refresh(); }, 3500);
})();
