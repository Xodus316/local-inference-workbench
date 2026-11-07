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
  function stageName(stage) {
    const labels = {build_plan: "Plan", build_tests: "Write independent tests", implement: "Implement"};
    if (labels[stage]) return labels[stage];
    const repair = /^(build_tests_syntax|build_tests_fixture|build_tests_coverage|build_strengthen|repair)_(\d+)$/.exec(stage);
    if (!repair) return stage;
    const names = {build_tests_syntax: "Repair test syntax", build_tests_fixture: "Repair test fixtures",
                   build_tests_coverage: "Complete test coverage", build_strengthen: "Strengthen tests", repair: "Repair implementation"};
    return names[repair[1]] + " (attempt " + repair[2] + ")";
  }
  function verifiedFiles(row) {
    const output = row?.result?.output;
    if (row?.bundle.request.mode !== "build" || row.status !== "completed" || !output?.artifact?.files?.length) return [];
    return output.artifact.files;
  }
  function selectedFile() {
    return verifiedFiles(selected).find(file => file.path === el("build-path").value);
  }
  function showFile() {
    el("build-file").textContent = selectedFile()?.content ?? "";
  }
  function download(name, content, type) {
    const url = URL.createObjectURL(new Blob([content], {type}));
    const link = document.createElement("a");
    link.download = name; link.href = url;
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  el("build-path").onchange = showFile;
  el("copy-file").onclick = async () => {
    const file = selectedFile();
    if (!file) return;
    try { await navigator.clipboard.writeText(file.content); message("Copied " + file.path); }
    catch { message("Clipboard access failed. Select and copy the displayed source."); }
  };
  el("download-file").onclick = () => {
    const file = selectedFile();
    if (file) download(file.path.split("/").pop(), file.content, "text/plain;charset=utf-8");
  };
  el("download-manifest").onclick = () => {
    const files = verifiedFiles(selected);
    if (files.length) download("build-" + selected.id + ".json", JSON.stringify({files}, null, 2), "application/json");
  };
  function renderBuild(row, previous) {
    const isBuild = row.bundle.request.mode === "build";
    const output = row.result?.output;
    el("build-output").hidden = !isBuild || !output;
    if (!isBuild || !output) {
      el("build-files").hidden = true;
      return;
    }
    if (previous?.id === row.id && previous.updated === row.updated && previous.status === row.status) return;
    const files = verifiedFiles(row);
    const strength = output.verification?.strength?.at(-1);
    let summary = output.artifact?.summary || "";
    if (output.error) summary = stageName(output.error.stage) + " · " + output.error.message;
    else if (strength?.reason) summary += " " + strength.reason.replaceAll("_", " ") + ".";
    if (Number.isInteger(output.repair_attempts)) summary += " Implementation repairs: " + output.repair_attempts + ".";
    el("build-summary").textContent = summary.trim();
    const diagnoses = Object.entries(row.result.stages || {}).filter(([, value]) => value?.diagnosis)
      .map(([stage, value]) => ({stage, diagnosis: value.diagnosis}));
    for (const [id, data] of [["plan", output.plan], ["suite", output.suite], ["diagnosis", diagnoses.length && diagnoses],
                              ["verification", output.verification]]) {
      el("build-" + id + "-block").hidden = !data;
      el("build-" + id).textContent = data ? JSON.stringify(data, null, 2) : "";
    }
    el("build-files").hidden = !files.length;
    const paths = el("build-path");
    const priorPath = previous?.id === row.id ? paths.value : "";
    paths.replaceChildren();
    for (const file of files) {
      const option = document.createElement("option");
      option.value = option.textContent = file.path;
      paths.append(option);
    }
    if (files.some(file => file.path === priorPath)) paths.value = priorPath;
    showFile();
  }
  function renderRun(row) {
    const previous = selected;
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
    if (previous?.id !== row.id) el("input-view").open = row.status === "prepared";
    el("calls").replaceChildren();
    for (const call of row.calls || []) {
      const item = document.createElement("div"); item.className = "call";
      const stage = request.mode === "build" ? stageName(call.stage) : call.stage;
      item.textContent = [stage, call.model, call.status].filter(Boolean).join(" · ");
      el("calls").append(item);
    }
    const result = row.result || {};
    const output = result.output ?? result.review ?? result;
    renderBuild(row, previous);
    el("result-block").hidden = !row.error && (!row.result || !el("build-output").hidden);
    el("result").textContent = el("result-block").hidden ? "" : row.error || (typeof output === "string" ? output : JSON.stringify(output, null, 2));
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
    try { renderRun(await api(path, typeof body === "function" ? await body() : body)); }
    catch (error) { message(error.message + ". Check Saved runs for the latest state."); }
    finally { busy = false; for (const id of buttons) el(id).disabled = false; await refresh(); }
  }
  el("mode").onchange = () => {
    const repo = ["edit", "plan"].includes(el("mode").value);
    el("repository-fields").hidden = !repo;
    el("repository").required = repo; el("files").required = repo;
    el("test-field").hidden = el("mode").value !== "edit" || !testNames.length;
    el("research-field").hidden = el("mode").value !== "balanced";
    el("build-help").hidden = el("mode").value !== "build";
    el("build-control").hidden = el("mode").value !== "build";
  };
  const route = new URLSearchParams(location.hash.slice(1));
  if (route.get("mode") === "build") el("mode").value = "build";
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
    const controls = mode === "build" ? Array.from(el("control-files").files) : [];
    if (!controls.length) return mutation("/api/runs", body);
    mutation("/api/runs", async () => {
      if (controls.length > 40 || controls.reduce((sum, file) => sum + file.size, 0) > 16000) {
        throw new Error("Choose up to 40 reference files totaling at most 16,000 bytes");
      }
      const utf8 = new TextDecoder("utf-8", {fatal: true, ignoreBOM: true});
      body.control_files = await Promise.all(controls.map(async file => ({
        path: file.name, content: utf8.decode(await file.arrayBuffer())
      })));
      return body;
    });
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
  const linked = route.get("run");
  if (linked && /^[0-9a-f-]{36}$/.test(linked)) select(linked);
  loadTests();
  el("refresh").onclick = refresh;
  refresh();
  setInterval(() => { if (!document.hidden) refresh(); }, 3500);
})();
