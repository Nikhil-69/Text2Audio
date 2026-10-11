// Batch mode: a folder of text files -> one audio file each -> merge / video of any files.
// The page is a live list: every file is a row with its own progress, and you can
// select any files (or a range like 1-25) to merge, make a video, regenerate or delete.
const Batch = {
  data: null,       // the batch being shown (kept in sync from server events)
  all: [],
  source: null,     // EventSource of the running job
  job: null,        // { id, action }
  filter: "all",
  sel: new Set(),   // selected file numbers (1-based positions)
  lastClicked: null,
  etaRef: null,     // { t, n } baseline for the overall ETA

  TEXT_RE: /\.(md|txt|docx|epub|pdf)$/i,

  // ---------- helpers ----------
  bytes(n) {
    if (!n) return "";
    const u = ["B", "KB", "MB", "GB"]; let i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return n.toFixed(i ? 1 : 0) + " " + u[i];
  },

  // [1,2,3,5] -> "1–3, 5"
  rangeLabel(nums) {
    const a = [...nums].sort((x, y) => x - y), parts = [];
    for (let i = 0; i < a.length;) {
      let j = i; while (j + 1 < a.length && a[j + 1] === a[j] + 1) j++;
      parts.push(j > i ? `${a[i]}–${a[j]}` : `${a[i]}`); i = j + 1;
    }
    return parts.join(", ");
  },

  async call(method, url, body) {
    const opts = { method };
    if (body !== undefined) { opts.headers = { "Content-Type": "application/json" }; opts.body = JSON.stringify(body); }
    return T2A.api(url, opts);
  },

  // Apply an edit; the server answers with the updated batch.
  async edit(method, url, body, { keepSelection = false } = {}) {
    try {
      const b = await this.call(method, url, body);
      if (!keepSelection) this.sel.clear();
      this.data = b;
      this.all = this.all.map(x => x.id === b.id ? b : x);
      this.paint();
      return b;
    } catch (e) {
      let msg = e.message || "failed";
      try { msg = JSON.parse(msg).detail || msg; } catch { /* plain text */ }
      T2A.toast(msg);
    }
  },

  get busy() { return Boolean(this.job); },
  doneItems() { return this.data.items.filter(i => i.status === "done"); },

  // ---------- page shell ----------
  async render() {
    const el = document.getElementById("tab-batch");
    el.innerHTML = `
      <div id="batch-head"></div>
      <details class="newbatch" id="newbatch">
        <summary>＋ New batch</summary>
        <div class="newbody">
          <div class="pickrow">
            <label class="btn pick">📁 Choose folder<input id="b-folder" type="file" webkitdirectory multiple hidden></label>
            <label class="btn pick">📄 Choose files<input id="b-files" type="file" multiple accept=".md,.txt,.docx,.epub,.pdf" hidden></label>
            <span class="muted" id="b-count">Nothing selected</span>
          </div>
          <div class="pickrow">
            <input id="b-title" placeholder="Batch name (optional)" aria-label="Batch name">
            <select id="b-voice" aria-label="Narrator voice">${T2A.state.voices.map(v =>
              `<option value="${T2A.esc(v.id)}" ${v.id === T2A.state.voice ? "selected" : ""}>${T2A.esc(v.label)}</option>`).join("")}</select>
            <button class="btn primary inline" id="b-create" disabled>Create batch</button>
          </div>
          <div class="pickrow">
            <label for="b-speed" class="muted">Speed · <span id="b-spd">${T2A.state.speed}</span>×</label>
            <input type="range" id="b-speed" min="0.5" max="1.5" step="0.05" value="${T2A.state.speed}" style="flex:1;min-width:160px">
          </div>
        </div>
      </details>
      <div id="batch-body"></div>`;
    const folder = document.getElementById("b-folder"), files = document.getElementById("b-files");
    this.picked = [];
    const onPick = () => {
      this.picked = [...folder.files, ...files.files].filter(f => this.TEXT_RE.test(f.name));
      document.getElementById("b-count").textContent =
        this.picked.length ? `${this.picked.length} text file(s) ready` : "No text files found";
      document.getElementById("b-create").disabled = !this.picked.length;
      const first = this.picked[0]?.webkitRelativePath?.split("/")[0];
      const title = document.getElementById("b-title");
      if (first && !title.value) title.value = first;
    };
    folder.onchange = files.onchange = onPick;
    document.getElementById("b-speed").oninput = e => {
      document.getElementById("b-spd").textContent = e.target.value; };
    document.getElementById("b-create").onclick = () => this.create();
    await this.load();
  },

  uploadForm(list) {
    const fd = new FormData();
    // The relative path is sent as the filename so files sort by folder, then name.
    list.forEach(f => fd.append("files", f, f.webkitRelativePath || f.name));
    return fd;
  },

  async create() {
    const btn = document.getElementById("b-create");
    btn.disabled = true; btn.textContent = "Uploading…";
    const fd = this.uploadForm(this.picked);
    fd.append("title", document.getElementById("b-title").value);
    fd.append("voice", document.getElementById("b-voice").value);
    fd.append("speed", document.getElementById("b-speed").value);
    try {
      const b = await T2A.api("/api/batches", { method: "POST", body: fd });
      document.getElementById("newbatch").open = false;
      T2A.toast(`Batch created: ${b.items.length} files`);
      this.filter = "all"; this.sel.clear();
      await this.load(b.id);
    } catch (e) { T2A.toast("Could not create batch: " + e.message); }
    btn.textContent = "Create batch"; btn.disabled = !this.picked.length;
  },

  async load(selectId) {
    try { this.all = await T2A.api("/api/batches"); } catch { this.all = []; }
    const body = document.getElementById("batch-body");
    if (!body) return;
    if (!this.all.length) {
      this.data = null;
      document.getElementById("batch-head").innerHTML = `<h2>Batch</h2>`;
      body.innerHTML = `<div class="empty">
        <div class="big">📚</div><h3>Turn a folder of text files into audio</h3>
        <p>Open <b>＋ New batch</b> above, choose a folder, and press <b>Create batch</b>.<br>
        You get one audio file per text file, and can merge any range of them or make a video.</p></div>`;
      document.getElementById("newbatch").open = true;
      return;
    }
    const id = selectId || this.data?.id;
    const changed = !this.data || this.data.id !== id;
    this.data = this.all.find(b => b.id === id) || this.all[0];
    if (changed) this.sel.clear();
    // A job may already be running (page refresh, second tab, another browser).
    const active = this.data.activeJob;
    if (active && !this.source) this.job = { id: active.jobId, action: active.action };
    this.paint();
    if (active && !this.source) this.attach(active.jobId, this.data.id, active.action);
  },

  // ---------- derived state ----------
  counts() {
    const c = { done: 0, error: 0, running: 0, pending: 0 };
    this.data.items.forEach(i => c[i.status] = (c[i.status] || 0) + 1);
    return c;
  },

  // Files an action applies to: the selection, or every file when nothing is selected.
  targets() {
    const items = this.data.items;
    return this.sel.size ? items.filter(i => this.sel.has(i.index)) : items;
  },

  // ---------- painting ----------
  paint() {
    const b = this.data, c = this.counts(), n = b.items.length, busy = this.busy;
    [...this.sel].forEach(i => { if (i > n) this.sel.delete(i); });
    const allDone = n > 0 && c.done === n;
    const total = b.items.reduce((s, i) => s + (i.durationSeconds || 0), 0);
    const pick = this.all.length > 1
      ? `<select id="b-pick" aria-label="Choose batch">${this.all.map(x =>
          `<option value="${x.id}" ${x.id === b.id ? "selected" : ""}>${T2A.esc(x.title)}</option>`).join("")}</select>` : "";
    document.getElementById("batch-head").innerHTML =
      `<div class="bh"><h2>${T2A.esc(b.title)}</h2>${pick}
         <label class="btn small pick addbtn ${busy ? "off" : ""}" title="${busy ? "Wait for the running job to finish" : "Add more chapters to the end of this batch"}">＋ Add files<input id="a-files" type="file" multiple accept=".md,.txt,.docx,.epub,.pdf" hidden ${busy ? "disabled" : ""}></label>
         <label class="btn small pick addbtn ${busy ? "off" : ""}" title="${busy ? "Wait for the running job to finish" : "Add every text file in a folder"}">＋ Add folder<input id="a-folder" type="file" webkitdirectory multiple hidden ${busy ? "disabled" : ""}></label>
         <button class="btn small" id="b-settings">⚙ Settings</button>
         <button class="btn small" id="b-del" ${busy ? "disabled" : ""}>Delete batch</button></div>
       <div class="panel settings" id="b-setpanel" hidden>
         <div class="pickrow">
           <input id="s-title" value="${T2A.esc(b.title)}" aria-label="Batch name" placeholder="Batch name">
           <input id="s-author" value="${T2A.esc(b.author)}" aria-label="Author" placeholder="Author">
           <select id="s-voice" aria-label="Narrator voice">${T2A.state.voices.map(v =>
             `<option value="${T2A.esc(v.id)}" ${v.id === b.voice ? "selected" : ""}>${T2A.esc(v.label)}</option>`).join("")}</select>
           <button class="btn" id="s-save" ${busy ? "disabled" : ""}>Save</button>
         </div>
         <div class="pickrow">
           <label for="s-speed" class="muted">Speed · <span id="s-spd">${b.speed}</span>×</label>
           <input type="range" id="s-speed" min="0.5" max="1.5" step="0.05" value="${b.speed}" style="flex:1;min-width:160px">
         </div>
         <p class="muted small">A new voice or speed applies to files generated or regenerated from now on.</p>
         </div>`;

    const step = (label, ok, active) =>
      `<li class="${ok ? "ok" : active ? "active" : ""}"><i>${ok ? "✓" : ""}</i>${label}</li>`;
    const hasAudio = b.outputs.some(o => o.kind === "audio"), hasVideo = b.outputs.some(o => o.kind === "video");
    const chip = (key, label, count) =>
      `<button class="chip ${this.filter === key ? "on" : ""}" data-f="${key}">${label} <b>${count}</b></button>`;
    const pending = n - c.done;

    document.getElementById("batch-body").innerHTML = `
      <div class="panel summary">
        <ol class="steps">${step("Audio for each file", allDone, !allDone)}${step("Merge (optional)", hasAudio, c.done > 0 && !hasAudio)}${step("Video (optional)", hasVideo, c.done > 0 && !hasVideo)}</ol>
        <div class="sumline"><span id="b-sumtext"></span><span id="b-pct" class="pct"></span></div>
        <div class="bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-label="Overall progress"><div class="barf" id="b-barf"></div></div>
        <div class="actions">
          ${pending ? `<button class="btn primary inline" id="b-synth" ${busy ? "disabled" : ""}>▶ ${c.done || c.error ? `Generate remaining audio (${pending})` : "Generate audio"}</button>` : ""}
          <button class="btn" id="b-cancel" ${busy ? "" : "hidden"}>Cancel</button>
        </div>
      </div>

      <div class="panel selbar">
        <div class="selrow">
          <span class="lbl">Select files</span>
          <input type="number" id="r-from" min="1" max="${n}" value="1" aria-label="From file number">
          <span>to</span>
          <input type="number" id="r-to" min="1" max="${n}" value="${Math.max(1, c.done || n)}" aria-label="To file number">
          <button class="btn small" id="r-go">Select range</button>
          <button class="btn small" id="r-done">All finished</button>
          <button class="btn small" id="r-all">All</button>
          <button class="btn small" id="r-none">None</button>
          <span class="muted selcount" id="b-selcount"></span>
        </div>
        <div class="selrow" id="b-selactions"></div>
      </div>

      <div id="b-outputs"></div>

      <div class="chips">${chip("all", "All", n)}${chip("done", "Done", c.done)}${chip("waiting", "Waiting", c.pending + c.running)}${chip("error", "Failed", c.error)}
        <span class="muted tot">${total ? "Total audio " + T2A.fmtDuration(total) : ""}</span></div>
      <ul class="rows" id="b-rows">${b.items.map(i => this.rowHtml(i)).join("")}</ul>`;

    this.bindShell();
    this.paintOutputs();
    this.paintSelection();
    this.updateSummary();
  },

  bindShell() {
    const b = this.data, $ = id => document.getElementById(id);
    const on = (id, fn) => { const e = $(id); if (e) e.onclick = fn; };
    const pickEl = $("b-pick");
    if (pickEl) pickEl.onchange = e => { this.data = this.all.find(x => x.id === e.target.value); this.sel.clear(); this.paint(); };
    on("b-settings", () => { $("b-setpanel").hidden = !$("b-setpanel").hidden; });
    on("b-del", () => this.delBatch());
    $("s-speed").oninput = e => { $("s-spd").textContent = e.target.value; };
    on("s-save", () => this.edit("PATCH", `/api/batches/${b.id}`,
      { title: $("s-title").value, author: $("s-author").value, voice: $("s-voice").value,
        speed: parseFloat($("s-speed").value) }, { keepSelection: true })
      .then(r => r && T2A.toast("Saved")));
    $("a-files").onchange = e => this.addFiles([...e.target.files]);
    $("a-folder").onchange = e => this.addFiles([...e.target.files]);
    on("b-synth", () => this.run("synthesize"));
    on("b-cancel", () => this.cancel());
    on("r-go", () => {
      let a = parseInt($("r-from").value, 10) || 1, z = parseInt($("r-to").value, 10) || a;
      if (a > z) [a, z] = [z, a];
      this.sel = new Set(b.items.filter(i => i.index >= a && i.index <= z).map(i => i.index));
      this.refreshSelection();
    });
    on("r-done", () => { this.sel = new Set(this.doneItems().map(i => i.index)); this.refreshSelection(); });
    on("r-all", () => { this.sel = new Set(b.items.map(i => i.index)); this.refreshSelection(); });
    on("r-none", () => { this.sel.clear(); this.refreshSelection(); });
    document.querySelectorAll(".chip").forEach(ch => ch.onclick = () => { this.filter = ch.dataset.f; this.paint(); });
    const rows = $("b-rows");
    rows.onclick = e => this.rowClick(e);
    const pane = document.getElementById("tab-batch");
    if (!pane._dropBound) {                       // bind once; the pane element is reused across repaints
      pane._dropBound = true;
      let depth = 0;
      pane.addEventListener("dragenter", e => { if (this.data && e.dataTransfer?.types?.includes("Files")) { depth++; pane.classList.add("dropping"); } });
      pane.addEventListener("dragleave", () => { if (--depth <= 0) { depth = 0; pane.classList.remove("dropping"); } });
      pane.addEventListener("dragover", e => { if (this.data && e.dataTransfer?.types?.includes("Files")) e.preventDefault(); });
      pane.addEventListener("drop", e => {
        if (!this.data || !e.dataTransfer?.files?.length) return;
        e.preventDefault(); depth = 0; pane.classList.remove("dropping");
        this.addFiles([...e.dataTransfer.files]);
      });
    }
  },

  paintSelection() {
    const b = this.data, busy = this.busy;
    const sel = this.targets(), selected = this.sel.size > 0;
    const done = sel.filter(i => i.status === "done");
    const label = selected ? `files ${this.rangeLabel(done.map(i => i.index))}` : "all finished files";
    const countEl = document.getElementById("b-selcount");
    if (countEl) countEl.textContent = selected ? `${this.sel.size} selected` : "Nothing selected — merge / video use all finished files";
    const box = document.getElementById("b-selactions");
    if (!box) return;
    const dis = (cond, why) => busy || cond ? `disabled title="${busy ? "Wait for the running job" : why}"` : "";
    const nDone = done.length;
    const skipped = selected ? sel.length - nDone : 0;   // only meaningful for an explicit selection
    box.innerHTML = `
      <button class="btn" id="a-merge" ${dis(!nDone, "No finished audio in the selection")}>🎧 Merge ${nDone ? `(${nDone})` : ""}</button>
      <button class="btn" id="a-video" ${dis(!nDone, "No finished audio in the selection")}>🎬 Make video ${nDone ? `(${nDone})` : ""}</button>
      <button class="btn" id="a-gen" ${dis(!selected, "Select files first")}>▶ Generate selected</button>
      <button class="btn" id="a-regen" ${dis(!selected, "Select files first")}>↻ Regenerate selected</button>
      <button class="btn danger" id="a-del" ${dis(!selected, "Select files first")}>🗑 Delete selected</button>
      <span class="muted small hint">${nDone ? `Merge / video use ${label}${skipped ? ` — ${skipped} without audio will be skipped` : ""}` : ""}</span>`;
    const on = (id, fn) => { const e = document.getElementById(id); if (e) e.onclick = fn; };
    const nums = () => [...this.sel].sort((x, y) => x - y);
    on("a-merge", () => this.run("merge", { items: done.map(i => i.index) }));
    on("a-video", () => this.videoDialog(done.map(i => i.index)));
    on("a-gen", () => this.run("synthesize", { items: nums() }));
    on("a-regen", async () => {
      if (!confirm(`Discard and regenerate audio for ${this.sel.size} file(s)?`)) return;
      const keep = nums();
      if (await this.edit("POST", `/api/batches/${b.id}/items/reset`, { items: keep }, { keepSelection: true }))
        this.run("synthesize", { items: keep });
    });
    on("a-del", () => {
      if (!confirm(`Delete ${this.sel.size} file(s) from this batch? Merged outputs you already made are kept.`)) return;
      this.edit("POST", `/api/batches/${b.id}/items/delete`, { items: nums() });
    });
  },

  // Re-sync checkboxes + toolbar after the selection changed (no full repaint).
  refreshSelection() {
    document.querySelectorAll("#b-rows .row").forEach(li => {
      const on = this.sel.has(+li.dataset.i);
      li.classList.toggle("sel", on);
      const cb = li.querySelector(".pick-cb"); if (cb) cb.checked = on;
    });
    this.paintSelection();
  },

  paintOutputs() {
    const b = this.data, box = document.getElementById("b-outputs");
    if (!box) return;
    if (!b.outputs.length) { box.innerHTML = ""; return; }
    box.innerHTML = `<div class="label" style="margin-top:16px">Merged audiobooks &amp; videos</div>
      <ul class="rows outputs">${b.outputs.slice().reverse().map(o => `
        <li class="row out" data-o="${o.id}">
          <span class="st kind">${o.kind === "video" ? "🎬" : "🎧"}</span>
          <div class="main"><div class="nm">${T2A.esc(o.name)}</div>
            <div class="sub">${o.count === 1 ? `file ${o.first}` : `files ${o.first}–${o.last}`} · ${o.count} file(s) · ${T2A.fmtDuration(o.durationSeconds)} · ${this.bytes(o.sizeBytes)} · ${o.kind === "video" ? (o.cover ? ".mp4 · with image" : ".mp4") : ".m4b"}</div></div>
          <div class="acts">
            <a class="ib" href="/api/batches/${b.id}/outputs/${o.id}" title="Download" aria-label="Download ${T2A.esc(o.name)}">⬇</a>
            <button class="ib o-ren" title="Rename" aria-label="Rename">✎</button>
            <button class="ib o-del" title="Delete" aria-label="Delete">🗑</button>
          </div></li>`).join("")}</ul>`;
    box.querySelectorAll(".o-ren").forEach(btn => btn.onclick = () => {
      const o = b.outputs.find(x => x.id === btn.closest(".row").dataset.o);
      const name = prompt("Rename", o.name);
      if (name && name.trim()) this.edit("PATCH", `/api/batches/${b.id}/outputs/${o.id}`, { name }, { keepSelection: true });
    });
    box.querySelectorAll(".o-del").forEach(btn => btn.onclick = () => {
      const o = b.outputs.find(x => x.id === btn.closest(".row").dataset.o);
      if (confirm(`Delete "${o.name}"? The individual file audio is kept.`))
        this.edit("DELETE", `/api/batches/${b.id}/outputs/${o.id}`, undefined, { keepSelection: true });
    });
  },

  visible(i) {
    return this.filter === "all" || (this.filter === "done" && i.status === "done")
      || (this.filter === "error" && i.status === "error")
      || (this.filter === "waiting" && (i.status === "pending" || i.status === "running"));
  },

  rowHtml(i) {
    const run = i.status === "running";
    const icon = { done: "✓", error: "!", running: "●", pending: "" }[i.status] || "";
    const pct = i.status === "done" ? 100 : (i.pct || 0);
    const sub = i.status === "done" ? T2A.fmtDuration(i.durationSeconds)
      : i.status === "error" ? `<span class="err">${T2A.esc(i.error || "failed")}</span>`
      : run ? `Generating… <span class="ipct">${pct}%</span>` : "Waiting";
    const act = i.status === "done"
      ? `<button class="ib play" title="Play" aria-label="Play ${T2A.esc(i.name)}">▶</button>
         <a class="ib" href="/api/batches/${this.data.id}/item/${i.index}/audio" title="Download" aria-label="Download ${T2A.esc(i.name)}">⬇</a>` : "";
    const sel = this.sel.has(i.index);
    return `<li class="row ${i.status} ${sel ? "sel" : ""}" data-i="${i.index}" ${this.visible(i) ? "" : "hidden"}>
      <input type="checkbox" class="pick-cb" ${sel ? "checked" : ""} aria-label="Select file ${i.index}">
      <span class="st">${icon}</span>
      <div class="main"><div class="nm"><span class="no">${i.index}</span>${T2A.esc(i.name)}</div>
        <div class="sub">${sub}</div>
        <div class="mini ${run ? "" : "off"}"><i style="width:${pct}%"></i></div></div>
      <div class="acts">${act}<button class="ib more" title="More" aria-label="More actions for ${T2A.esc(i.name)}">⋯</button></div></li>`;
  },

  patchRow(i) {
    const li = document.querySelector(`#b-rows .row[data-i="${i.index}"]`);
    if (li) li.outerHTML = this.rowHtml(i);
  },

  updateSummary() {
    const b = this.data, c = this.counts(), n = b.items.length;
    const txt = document.getElementById("b-sumtext");
    if (!txt) return;
    let pct = n ? Math.round(c.done / n * 100) : 0, msg;
    if (this.job && this.job.action === "synthesize") {
      msg = this.job.msg || "Starting…";
      pct = this.job.pct ?? pct;
    } else if (this.job) {
      msg = this.job.msg || (this.job.action === "merge" ? "Merging audio… starting"
        : "Rendering video… starting");
      pct = this.job.pct ?? 0;
    } else {
      msg = !n ? "No files in this batch" : c.done === n ? "All audio is ready" : `${c.done} of ${n} files done`
        + (c.error ? ` · ${c.error} failed` : "");
    }
    txt.textContent = msg;
    document.getElementById("b-pct").textContent = pct + "%";
    document.getElementById("b-barf").style.width = pct + "%";
    document.querySelector(".summary .bar")?.setAttribute("aria-valuenow", String(pct));
  },

  // ---------- row interactions ----------
  rowClick(e) {
    const li = e.target.closest(".row");
    if (!li) return;
    const index = +li.dataset.i;
    if (e.target.classList.contains("pick-cb")) {
      // shift-click selects everything between the last click and this one
      if (e.shiftKey && this.lastClicked) {
        const [a, z] = [this.lastClicked, index].sort((x, y) => x - y);
        this.data.items.filter(i => i.index >= a && i.index <= z).forEach(i => this.sel.add(i.index));
      } else if (e.target.checked) this.sel.add(index); else this.sel.delete(index);
      this.lastClicked = index;
      this.refreshSelection();
      return;
    }
    if (e.target.closest(".play")) return this.togglePlayer(li, index);
    if (e.target.closest(".more")) return this.openMenu(li, index, e.target.closest(".more"));
  },

  togglePlayer(li, index) {
    const existing = li.querySelector("audio");
    document.querySelectorAll("#b-rows audio").forEach(a => a.remove());
    if (existing) return;
    const a = document.createElement("audio");
    a.controls = true; a.autoplay = true; a.className = "inplayer";
    a.src = `/api/batches/${this.data.id}/item/${index}/audio`;
    li.querySelector(".main").appendChild(a);
  },

  openMenu(li, index, anchor) {
    document.querySelectorAll(".rowmenu").forEach(m => m.remove());
    const n = this.data.items.length, it = this.data.items[index - 1], busy = this.busy;
    const m = document.createElement("div");
    m.className = "rowmenu";
    const item = (id, label, off) => `<button data-a="${id}" ${off || busy ? "disabled" : ""}>${label}</button>`;
    m.innerHTML = item("rename", "✎ Rename") + item("text", "📝 Edit text")
      + item("regen", "↻ Regenerate audio", it.status === "pending")
      + item("up", "↑ Move up", index === 1) + item("down", "↓ Move down", index === n)
      + item("to", "⇅ Move to position…") + item("del", "🗑 Delete file");
    li.appendChild(m);
    const close = ev => { if (!m.contains(ev.target)) { m.remove(); document.removeEventListener("click", close, true); } };
    setTimeout(() => document.addEventListener("click", close, true), 0);
    m.onclick = async ev => {
      const a = ev.target.dataset.a; if (!a) return;
      m.remove(); document.removeEventListener("click", close, true);
      const b = this.data, url = `/api/batches/${b.id}/items/${index}`;
      if (a === "rename") {
        const name = prompt("File name", it.name);
        if (name && name.trim()) this.edit("PATCH", url, { name }, { keepSelection: true });
      } else if (a === "text") this.editText(index, it);
      else if (a === "regen") {
        if (await this.edit("POST", `/api/batches/${b.id}/items/reset`, { items: [index] }, { keepSelection: true }))
          this.run("synthesize", { items: [index] });
      } else if (a === "up") this.edit("POST", `${url}/move`, { to: index - 1 });
      else if (a === "down") this.edit("POST", `${url}/move`, { to: index + 1 });
      else if (a === "to") {
        const to = parseInt(prompt(`Move to position (1–${n})`, String(index)), 10);
        if (to >= 1 && to <= n && to !== index) this.edit("POST", `${url}/move`, { to });
      } else if (a === "del") {
        if (confirm(`Delete file ${index} "${it.name}"? Merged outputs you already made are kept.`))
          this.edit("POST", `/api/batches/${b.id}/items/delete`, { items: [index] });
      }
    };
  },

  async editText(index, it) {
    let text;
    try { ({ text } = await T2A.api(`/api/batches/${this.data.id}/items/${index}/text`)); }
    catch (e) { T2A.toast("Could not load text"); return; }
    const dlg = document.createElement("dialog");
    dlg.className = "editdlg";
    dlg.innerHTML = `<form method="dialog">
      <h3>Edit text · ${index}. ${T2A.esc(it.name)}</h3>
      ${it.status === "done" ? `<p class="muted small">Saving discards this file's current audio; generate it again afterwards.</p>` : ""}
      <textarea rows="16" spellcheck="true"></textarea>
      <div class="pickrow end"><button class="btn" value="cancel">Cancel</button>
      <button class="btn primary inline" value="save">Save</button></div></form>`;
    dlg.querySelector("textarea").value = text;
    document.body.appendChild(dlg);
    dlg.addEventListener("close", async () => {
      const save = dlg.returnValue === "save", val = dlg.querySelector("textarea").value;
      dlg.remove();
      if (save && val !== text) await this.edit("PATCH", `/api/batches/${this.data.id}/items/${index}`, { text: val }, { keepSelection: true });
    });
    dlg.showModal();
  },

  // "Make video": pick (or keep) the background image, optionally name the video.
  videoDialog(indexes) {
    const id = this.data.id;
    const dlg = document.createElement("dialog");
    dlg.className = "editdlg";
    dlg.innerHTML = `<form method="dialog">
      <h3>Make video · ${indexes.length === 1 ? `file ${indexes[0]}` : `files ${this.rangeLabel(indexes)}`}</h3>
      <div class="vidimg">
        <div class="thumb" id="v-thumb"></div>
        <div class="vidctl">
          <label class="btn pick">🖼 Add images<input id="v-file" type="file" multiple accept="image/jpeg,image/png,image/webp" hidden></label>
          <p class="muted small" id="v-note"></p>
        </div>
      </div>
      <div class="field"><label for="v-name">Video name (optional)</label>
        <input id="v-name" placeholder="${T2A.esc(this.data.title)} - files ${this.rangeLabel(indexes)} (video)"></div>
      <div class="pickrow end"><button class="btn" value="cancel">Cancel</button>
        <button class="btn primary inline" value="make">Make video</button></div></form>`;
    document.body.appendChild(dlg);
    const $ = sel => dlg.querySelector(sel);
    let ids = [];
    const show = list => {
      ids = list;
      $("#v-thumb").innerHTML = ids.length
        ? ids.map(i => `<span class="vthumb"><img alt="Video image" src="/api/batches/${id}/images/${i}?t=${Date.now()}">
            <button type="button" class="x" data-id="${i}" aria-label="Remove image">✕</button></span>`).join("")
        : `<span>No image</span>`;
      $("#v-note").textContent = ids.length > 1
        ? `${ids.length} images play one after another, each for an equal share of the video (shown in full on black, 1920×1080).`
        : ids.length ? "This image is used as the video background (shown in full on black, 1920×1080). Add more for a slideshow."
        : "Without an image the video has a plain black background. JPEG, PNG or WebP, up to 10 MB each. Add several for a slideshow.";
    };
    const fail = err => {
      let msg = err.message; try { msg = JSON.parse(msg).detail || msg; } catch { /* plain */ }
      T2A.toast(msg);
    };
    T2A.api(`/api/batches/${id}/images`).then(r => show(r.images)).catch(() => show([]));
    show([]);
    $("#v-file").onchange = async e => {
      const files = [...e.target.files]; if (!files.length) return;
      const fd = new FormData(); files.forEach(f => fd.append("images", f));
      $("#v-note").textContent = "Uploading…";
      try { show((await T2A.api(`/api/batches/${id}/images`, { method: "POST", body: fd })).images); }
      catch (err) { fail(err); show(ids); }
      e.target.value = "";
    };
    $("#v-thumb").onclick = async e => {
      const btn = e.target.closest("button.x"); if (!btn) return;
      try { show((await T2A.api(`/api/batches/${id}/images/${btn.dataset.id}`, { method: "DELETE" })).images); }
      catch (err) { fail(err); }
    };
    dlg.addEventListener("close", () => {
      const make = dlg.returnValue === "make", name = $("#v-name").value.trim();
      dlg.remove();
      if (make) this.run("video", { items: indexes, name: name || null });
    });
    dlg.showModal();
  },

  async addFiles(list) {
    if (this.busy) { T2A.toast("Wait for the running job to finish (or cancel it) before adding files"); return; }
    const picked = list.filter(f => this.TEXT_RE.test(f.name));
    if (!picked.length) { T2A.toast("No text files found (use .txt, .md, .docx, .epub or .pdf)"); return; }
    const before = this.data.items.length;
    T2A.toast(`Uploading ${picked.length} file(s)…`);
    try {
      const b = await T2A.api(`/api/batches/${this.data.id}/files`, { method: "POST", body: this.uploadForm(picked) });
      this.data = b; this.all = this.all.map(x => x.id === b.id ? b : x);
      const added = b.items.length - before;
      // Select the new chapters so "Generate selected" / the main button picks them up at once.
      this.sel = new Set(b.items.filter(i => i.index > before).map(i => i.index));
      this.filter = "all";
      this.paint();
      T2A.toast(`Added ${added} chapter(s) at the end — they're selected; press Generate to create their audio`);
      document.querySelector(`#b-rows .row[data-i="${before + 1}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" });
    } catch (e) {
      let msg = e.message || "failed"; try { msg = JSON.parse(msg).detail || msg; } catch { /* plain text */ }
      T2A.toast("Could not add files: " + msg);
    }
  },


  async delBatch() {
    if (!confirm(`Delete the whole batch "${this.data.title}", all its audio and its merged files?`)) return;
    try { await T2A.api(`/api/batches/${this.data.id}`, { method: "DELETE" }); this.data = null; await this.load(); }
    catch (e) { T2A.toast("Could not delete: " + e.message); }
  },

  // ---------- running jobs ----------
  async run(action, body) {
    try {
      const { jobId } = await this.call("POST", `/api/batches/${this.data.id}/${action}`, body || {});
      this.job = { id: jobId, action };
      this.paint();
      this.attach(jobId, this.data.id, action);
    } catch (e) {
      let msg = e.message || "Could not start";
      try { msg = JSON.parse(msg).detail || msg; } catch { /* plain text */ }
      T2A.toast(msg);
    }
  },

  async cancel() {
    if (!this.job || !confirm("Cancel? Files already finished are kept.")) return;
    try {
      await T2A.api(`/api/render/${this.job.id}`, { method: "DELETE" });
      this.job.msg = "Cancelling…"; this.updateSummary();
    } catch (e) { T2A.toast(e.message); }
  },

  attach(jobId, bid, action) {
    if (this.source) this.source.close();
    this.etaRef = null;
    const src = this.source = new EventSource(`/api/render/${jobId}/stream`);
    const finish = async msg => {
      src.close(); this.source = null; this.job = null;
      if (msg) T2A.toast(msg);
      await this.load(bid);
    };
    src.onmessage = ev => {
      const e = JSON.parse(ev.data);
      if (!this.data || this.data.id !== bid) return;
      if (e.type === "progress") {
        this.job.pct = e.percent;
        if (e.phase === "audio") {
          const it = this.data.items.find(x => x.index === e.itemIndex);
          if (it) {
            it.status = "running";
            it.pct = Math.min(99, Math.round(e.itemChunksDone / e.itemChunkCount * 100));
            this.patchRow(it);
          }
          const now = (e.ts || Date.now() / 1000) * 1000;   // server time: replayed events stay accurate
          if (!this.etaRef) this.etaRef = { t: now, n: e.chunksDone };
          const n = e.chunksDone - this.etaRef.n, secs = (now - this.etaRef.t) / 1000;
          const eta = n >= 3 && secs > 0
            ? "ETA " + T2A.fmtDuration((e.chunkCount - e.chunksDone) / (n / secs)) : "estimating time…";
          this.job.msg = `Generating file ${e.chapterIndex + 1} of ${e.chapterCount} · ${eta}`;
        } else if (e.fraction != null) {
          // merge / video: ETA from how fast the encoder is moving (server timestamps)
          const now = (e.ts || Date.now() / 1000) * 1000;
          if (e.fraction > 0.01 && !this.etaRef) this.etaRef = { t: now, f: e.fraction };
          let eta = "estimating time…";
          if (this.etaRef && e.fraction > this.etaRef.f + 0.005) {
            const rate = (e.fraction - this.etaRef.f) / ((now - this.etaRef.t) / 1000);
            eta = "ETA " + T2A.fmtDuration((1 - e.fraction) / rate);
          }
          this.job.msg = `${e.chapterTitle}… ${eta}`;
        }
        this.updateSummary();
      } else if (e.type === "item") {
        const it = this.data.items.find(x => x.index === e.index);
        if (it) { Object.assign(it, { status: e.status, durationSeconds: e.durationSeconds, error: e.error, pct: 100 }); this.patchRow(it); }
        this.refreshChips();
      } else if (e.type === "done") {
        finish(e.failed ? `Finished — ${e.failed} file(s) failed` : "Done ✓");
      } else if (e.type === "error") {
        finish("Stopped: " + (e.message || "failed") + " — finished files are kept");
      }
    };
    src.onerror = () => { if (src.readyState === EventSource.CLOSED) finish("Connection lost"); };
  },

  refreshChips() {
    const c = this.counts(), n = this.data.items.length;
    const vals = { all: n, done: c.done, waiting: c.pending + c.running, error: c.error };
    document.querySelectorAll(".chip").forEach(ch => { ch.querySelector("b").textContent = vals[ch.dataset.f]; });
  },
};
