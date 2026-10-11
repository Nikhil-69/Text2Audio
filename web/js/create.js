const Create = {
  render() {
    const el = document.getElementById("tab-create");
    const opt = v => `<option value="${T2A.esc(v.id)}" ${v.id === T2A.state.voice ? "selected" : ""}>${T2A.esc(v.label)}${v.kind === "cloned" ? " (cloned)" : ` · ${T2A.esc(v.accent)} ${T2A.esc(v.gender)}`}</option>`;
    const presets = T2A.state.voices.filter(v => v.kind !== "cloned").map(opt).join("");
    const cloned = T2A.state.voices.filter(v => v.kind === "cloned").map(opt).join("");
    const voiceOpts = presets + (cloned ? `<optgroup label="Your voices">${cloned}</optgroup>` : "");
    el.innerHTML = `
      <h2>Create audiobook</h2>
      <p class="subtitle">Drop in manuscripts (.txt, Markdown, DOCX, EPUB or PDF), set options, and generate.</p>
      ${Onboarding.markup()}
      <div class="grid2">
        <div class="panel">
          <div class="label">Source files</div>
          <div class="dropzone" id="dz">Drop manuscript files here, or click to browse
            <input type="file" id="fi" multiple accept=".md,.txt,.docx,.epub,.pdf" hidden></div>
          <button class="btn example" id="use-example" type="button">Use safe example</button>
          <div class="filelist" id="fl"></div>
        </div>
        <div class="panel">
          <div class="label">Settings</div>
          <div class="field"><label for="f-title">Title</label><input id="f-title" placeholder="My Book" value="${T2A.esc(this.savedSetting("title"))}"></div>
          <div class="field"><label for="f-author">Author</label><input id="f-author" placeholder="Author name" value="${T2A.esc(this.savedSetting("author"))}"></div>
          <div class="field"><label for="f-cover">Cover artwork (optional)</label><input id="f-cover" type="file" accept="image/jpeg,image/png,image/webp"></div>
          <div class="field"><label for="f-voice">Narrator voice</label>
            <div class="voicepick"><select id="f-voice">${voiceOpts}</select>
              <button class="btn" id="f-prev">▶ Preview</button></div></div>
          <div class="field"><label for="f-speed">Speed · <span id="spd">${T2A.state.speed}</span>×</label>
            <input type="range" id="f-speed" min="0.5" max="1.5" step="0.05" value="${T2A.state.speed}"></div>
          <button class="btn primary" id="gen" disabled>Generate Audiobook</button>
          <div class="progress" id="prog" ${T2A.state.activeJob ? "" : "hidden"}>
            <div class="bar" role="progressbar" aria-label="Audiobook render progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${T2A.state.renderProgress.percent}"><div class="barf" id="barf" style="width:${T2A.state.renderProgress.percent}%"></div></div>
            <div class="meta" aria-live="polite"><span id="pmsg">${T2A.esc(T2A.state.renderProgress.message)}</span><span id="ppct">${T2A.state.renderProgress.percent}%</span></div>
            <button class="btn" id="cancel-render" type="button">Cancel render</button>
          </div>
        </div>
      </div>
      <div class="panel" style="margin-top:20px">
        <div class="label">Detected chapters <span id="chcount" class="muted"></span></div>
        <div class="chaplist" id="chaps"><span class="muted">Add files to see chapters.</span></div>
        <details id="book-editor" style="margin-top:14px">
          <summary>Edit prepared manuscript and chapter markers</summary>
          <label for="book-text" class="muted">Use lines beginning with ## to define chapter titles.</label>
          <textarea id="book-text" rows="14" style="width:100%;margin-top:8px" spellcheck="true">${T2A.esc(T2A.state.bookText)}</textarea>
          <button class="btn" id="apply-text" type="button" style="margin-top:8px">Apply manuscript edits</button>
        </details>
      </div>`;
    this.bind();
    Onboarding.bind();
  },

  savedSetting(name) {
    try { return JSON.parse(localStorage.getItem("text2audio.settings") || "{}")[name] || ""; }
    catch { return ""; }
  },

  saveSettings() {
    const settings = {
      title: document.getElementById("f-title")?.value || "",
      author: document.getElementById("f-author")?.value || "",
      voice: T2A.state.voice,
      speed: T2A.state.speed,
    };
    localStorage.setItem("text2audio.settings", JSON.stringify(settings));
  },

  bind() {
    const dz = document.getElementById("dz"), fi = document.getElementById("fi");
    dz.onclick = () => fi.click();
    fi.onchange = () => this.addFiles([...fi.files]);
    dz.ondragover = e => { e.preventDefault(); dz.classList.add("drag"); };
    dz.ondragleave = () => dz.classList.remove("drag");
    dz.ondrop = e => { e.preventDefault(); dz.classList.remove("drag"); this.addFiles([...e.dataTransfer.files]); };
    document.getElementById("use-example").onclick = () => this.loadExample();
    document.getElementById("f-voice").onchange = e => { T2A.state.voice = e.target.value; this.saveSettings(); };
    document.getElementById("f-speed").oninput = e => {
      T2A.state.speed = parseFloat(e.target.value); document.getElementById("spd").textContent = e.target.value; this.saveSettings(); };
    document.getElementById("f-title").oninput = () => this.saveSettings();
    document.getElementById("f-author").oninput = () => this.saveSettings();
    document.getElementById("f-prev").onclick = () => this.preview();
    document.getElementById("gen").onclick = () => this.generate();
    document.getElementById("apply-text").onclick = () => this.applyEditedText();
    document.getElementById("cancel-render").onclick = () => this.cancelRender();
    this.renderFiles();
  },

  async loadExample() {
    try {
      const r = await fetch("/examples/sample-book.txt");
      if (!r.ok) throw new Error("example unavailable");
      const text = await r.text();
      const file = new File([text], "safe-example-book.txt", { type: "text/plain" });
      T2A.state.files = [];
      this.addFiles([file]);
      document.getElementById("f-title").value = "A Quiet Village";
      document.getElementById("f-author").value = "Synthetic Example";
      this.saveSettings();
      Onboarding.advanceTo(1);
      T2A.toast("Safe example loaded");
    } catch { T2A.toast("Could not load the safe example"); }
  },

  addFiles(fileList) {
    const wanted = fileList.filter(f => /\.(md|txt|docx|epub|pdf)$/i.test(f.name));
    T2A.state.files.push(...wanted);
    T2A.state.bookText = ""; T2A.state.ingestReady = false;
    T2A.state.files.sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
    this.renderFiles(); this.refreshChapters();
  },

  renderFiles() {
    const fl = document.getElementById("fl");
    fl.innerHTML = T2A.state.files.map((f, i) =>
      `<div class="filerow" draggable="true" data-i="${i}">
         <span class="grip">⋮⋮</span><span class="nm">${T2A.esc(f.name)}</span>
         <span class="wc">${Math.round(f.size / 6)}w</span>
         <button class="x up" data-i="${i}" aria-label="Move ${T2A.esc(f.name)} up">↑</button>
         <button class="x down" data-i="${i}" aria-label="Move ${T2A.esc(f.name)} down">↓</button>
         <button class="x remove" data-x="${i}" aria-label="Remove ${T2A.esc(f.name)}">✕</button></div>`).join("");
    fl.querySelectorAll(".remove").forEach(b => b.onclick = () => {
      T2A.state.files.splice(+b.dataset.x, 1); T2A.state.bookText = "";
      T2A.state.ingestReady = false; this.renderFiles(); this.refreshChapters(); });
    fl.querySelectorAll(".up,.down").forEach(b => b.onclick = () => {
      const from = +b.dataset.i, to = from + (b.classList.contains("up") ? -1 : 1);
      if (to < 0 || to >= T2A.state.files.length) return;
      const [file] = T2A.state.files.splice(from, 1); T2A.state.files.splice(to, 0, file);
      T2A.state.bookText = ""; T2A.state.ingestReady = false;
      this.renderFiles(); this.refreshChapters();
    });
    this.enableReorder(fl);
    document.getElementById("gen").disabled = Boolean(T2A.state.activeJob) ||
      !T2A.state.files.length || !T2A.state.ingestReady;
  },

  enableReorder(fl) {
    let dragI = null;
    fl.querySelectorAll(".filerow").forEach(row => {
      row.ondragstart = () => { dragI = +row.dataset.i; row.classList.add("dragging"); };
      row.ondragend = () => row.classList.remove("dragging");
      row.ondragover = e => e.preventDefault();
      row.ondrop = e => {
        e.preventDefault(); const dropI = +row.dataset.i;
        const arr = T2A.state.files; const [m] = arr.splice(dragI, 1); arr.splice(dropI, 0, m);
        T2A.state.bookText = ""; T2A.state.ingestReady = false;
        this.renderFiles(); this.refreshChapters();
      };
    });
  },

  async refreshChapters() {
    const version = ++T2A.state.ingestVersion;
    const chaps = document.getElementById("chaps");
    if (!T2A.state.files.length) { chaps.innerHTML = `<span class="muted">Add files to see chapters.</span>`;
      T2A.state.bookText = ""; T2A.state.ingestReady = false; T2A.state.chapters = [];
      document.getElementById("chcount").textContent = ""; return false; }
    T2A.state.ingestReady = false;
    document.getElementById("gen").disabled = true;
    const fd = new FormData();
    T2A.state.files.forEach(f => fd.append("files", f));
    try {
      const data = await T2A.api("/api/ingest", { method: "POST", body: fd });
      if (version !== T2A.state.ingestVersion) return false;
      T2A.state.bookText = data.bookText; T2A.state.chapters = data.chapters;
      document.getElementById("book-text").value = data.bookText;
      T2A.state.ingestReady = data.chapters.length > 0;
      if (T2A.state.ingestReady) Onboarding.advanceTo(2);
      document.getElementById("gen").disabled = !T2A.state.ingestReady;
      document.getElementById("chcount").textContent = `· ${data.chapters.length}`;
      chaps.innerHTML = data.chapters.map(c =>
        `<div class="chapline"><span class="ci">${c.index + 1}</span>
         <span class="ct">${T2A.esc(c.title)}</span><span class="cc">${c.chars.toLocaleString()} chars</span></div>`).join("");
      return T2A.state.ingestReady;
    } catch (e) {
      if (version === T2A.state.ingestVersion) {
        T2A.state.bookText = ""; T2A.state.chapters = []; T2A.state.ingestReady = false;
        document.getElementById("gen").disabled = true; T2A.toast("Ingest failed: " + e.message);
      }
      return false;
    }
  },

  async preview() {
    const btn = document.getElementById("f-prev"); btn.textContent = "…";
    try {
      const r = await fetch("/api/voice-preview", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ voice: T2A.state.voice }) });
      if (!r.ok) throw new Error(await r.text());
      const blob = await r.blob(); const url = URL.createObjectURL(blob); const audio = new Audio(url);
      audio.onended = audio.onerror = () => URL.revokeObjectURL(url); await audio.play();
    } catch (e) { T2A.toast("Preview failed"); }
    btn.textContent = "▶ Preview";
  },

  async generate() {
    if (!T2A.state.ingestReady && !await this.refreshChapters()) return;
    const gen = document.getElementById("gen"); gen.disabled = true;
    const prog = document.getElementById("prog"); prog.hidden = false;
    const body = {
      bookText: T2A.state.bookText, voice: T2A.state.voice, speed: T2A.state.speed,
      title: document.getElementById("f-title").value, author: document.getElementById("f-author").value };
    let jobId;
    try {
      const cover = document.getElementById("f-cover").files[0];
      let res;
      if (cover) {
        const form = new FormData();
        Object.entries(body).forEach(([key, value]) => form.append(key, value));
        form.append("cover", cover);
        res = await T2A.api("/api/render-upload", { method: "POST", body: form });
      } else {
        res = await T2A.api("/api/render", { method: "POST",
          headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      }
      jobId = res.jobId;
      localStorage.setItem("text2audio.activeJob", jobId);
      T2A.state.activeJob = jobId;
      Onboarding.advanceTo(3);
    } catch (e) { T2A.toast("Could not start render"); gen.disabled = false; return; }

    this.attachRender(jobId);
  },

  async resumeRender(jobId) {
    try {
      await T2A.api(`/api/render/${jobId}/resume`, { method: "POST" });
      document.getElementById("gen").disabled = true;
      document.getElementById("pmsg").textContent = "Resuming from saved audio…";
      localStorage.setItem("text2audio.activeJob", jobId);
      this.attachRender(jobId);
    } catch (e) { T2A.toast("Could not resume: " + e.message); }
  },

  // Offer to finish renders that were interrupted (crash, closed terminal, reboot).
  async offerIncomplete() {
    try {
      const { items } = await T2A.api("/api/incomplete");
      if (!items.length || T2A.state.activeJob) return;
      const it = items[0];
      if (confirm(`"${it.title}" was interrupted (${it.chaptersDone} chapter(s) already saved). Resume it?`)) {
        document.getElementById("prog").hidden = false;
        this.resumeRender(it.id);
      }
    } catch (_) { /* optional nicety */ }
  },

  resumeActiveRender() {
    const jobId = T2A.state.activeJob || localStorage.getItem("text2audio.activeJob");
    if (!jobId || !/^[0-9a-f]{6,32}$/.test(jobId)) return;
    const prog = document.getElementById("prog");
    const gen = document.getElementById("gen");
    prog.hidden = false; gen.disabled = true;
    document.getElementById("pmsg").textContent = "Reconnecting to the current render…";
    this.attachRender(jobId, true);
  },

  attachRender(jobId, resumed = false) {
    if (T2A.state.activeSource && T2A.state.activeJob === jobId) return;
    if (T2A.state.activeSource) T2A.state.activeSource.close();
    T2A.state.activeJob = jobId;
    const gen = document.getElementById("gen");
    let rate0 = null;
    const src = new EventSource(`/api/render/${jobId}/stream`);
    T2A.state.activeSource = src;
    src.onmessage = ev => {
      const e = JSON.parse(ev.data);
      if (e.type === "progress") {
        document.getElementById("barf").style.width = e.percent + "%";
        document.getElementById("ppct").textContent = e.percent + "%";
        const now = Date.now();
        let etaText = "estimating…";
        if (e.phase === "video") {
          etaText = "assembling video (almost done)";
        } else if (e.chunkCount) {
          // Rate over chunks synthesized since this page attached (cached or
          // already-finished chapters would otherwise make the ETA far too short).
          if (!rate0) rate0 = { t: now, n: e.chunksDone };
          const done = e.chunksDone - rate0.n, secs = (now - rate0.t) / 1000;
          if (done >= 3 && secs > 0) {
            etaText = "ETA " + T2A.fmtDuration((e.chunkCount - e.chunksDone) / (done / secs));
          }
        }
        document.getElementById("pmsg").textContent =
          `Chapter ${e.chapterIndex + 1} of ${e.chapterCount} · ${e.chapterTitle} — ${etaText}`;
        T2A.state.renderProgress = { percent: e.percent,
          message: document.getElementById("pmsg").textContent };
        document.querySelector("#prog .bar")?.setAttribute("aria-valuenow", String(e.percent));
      } else if (e.type === "done") {
        document.getElementById("barf").style.width = "100%";
        document.getElementById("ppct").textContent = "100%";
        document.getElementById("pmsg").textContent = "Done ✓";
        src.close(); gen.disabled = false; localStorage.removeItem("text2audio.activeJob");
        T2A.state.activeJob = null; T2A.state.activeSource = null;
        T2A.state.firstValue = true; Onboarding.complete();
        T2A.toast(resumed ? "Render recovered — audiobook ready" : "Audiobook ready"); T2A.showTab("library");
      } else if (e.type === "error") {
        const pmsg = document.getElementById("pmsg");
        pmsg.textContent = "Error: " + (e.message || "render failed") + " ";
        if (/Resume/.test(e.message || "")) {
          const btn = document.createElement("button");
          btn.type = "button"; btn.textContent = "Resume";
          btn.onclick = () => this.resumeRender(jobId);
          pmsg.appendChild(btn);
        }
        src.close(); gen.disabled = false; localStorage.removeItem("text2audio.activeJob");
        T2A.state.activeJob = null; T2A.state.activeSource = null;
      }
    };
    src.onerror = () => {
      if (src.readyState === EventSource.CLOSED) {
        gen.disabled = false; document.getElementById("pmsg").textContent = "Connection lost";
      } else {
        document.getElementById("pmsg").textContent = "Reconnecting to render…";
      }
    };
  },

  async applyEditedText() {
    const text = document.getElementById("book-text").value;
    try {
      const data = await T2A.api("/api/parse-text", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, title: document.getElementById("f-title").value }) });
      T2A.state.bookText = text; T2A.state.chapters = data.chapters;
      T2A.state.ingestReady = true;
      document.getElementById("chcount").textContent = `· ${data.chapters.length}`;
      document.getElementById("chaps").innerHTML = data.chapters.map(c =>
        `<div class="chapline"><span class="ci">${c.index + 1}</span><span class="ct">${T2A.esc(c.title)}</span><span class="cc">${c.chars.toLocaleString()} chars</span></div>`).join("");
      document.getElementById("gen").disabled = Boolean(T2A.state.activeJob);
      T2A.toast("Manuscript edits applied");
    } catch (e) { T2A.toast("Could not apply edits: " + e.message); }
  },

  async cancelRender() {
    const jobId = T2A.state.activeJob;
    if (!jobId || !confirm("Cancel the current render? Partial output will be removed.")) return;
    try {
      await T2A.api(`/api/render/${jobId}`, { method: "DELETE" });
      document.getElementById("pmsg").textContent = "Cancelling…";
    } catch (e) { T2A.toast("Could not cancel: " + e.message); }
  },
};
