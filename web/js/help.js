const GUIDE_STEPS = [
  ["Add your text", "Choose .txt or Markdown files. New here? Use the safe example."],
  ["Check the chapters", "Read the detected chapter list and drag files into the right order."],
  ["Choose a narrator", "Pick a voice and use Preview before starting a long render."],
  ["Generate", "Keep Text2Audio running. You can refresh this page and reconnect to the active job."],
  ["Listen in Library", "A finished .m4b and its manifest are durable first value — not just opening this page."],
];

const Onboarding = {
  step() {
    const n = Number(localStorage.getItem("text2audio.guideStep") || 0);
    return Math.max(0, Math.min(GUIDE_STEPS.length - 1, n));
  },
  markup() {
    if (localStorage.getItem("text2audio.guideDismissed") === "1" && !T2A.state.firstValue) {
      return `<button class="guide-reopen" id="guide-reopen" type="button">Show beginner guide</button>`;
    }
    const i = T2A.state.firstValue ? 4 : this.step();
    const [title, detail] = GUIDE_STEPS[i];
    return `<aside class="quickstart" aria-labelledby="guide-title">
      <div class="guide-top"><span id="guide-count">BEGINNER GUIDE · ${i + 1} OF 5</span><button id="guide-skip" type="button">Skip guide</button></div>
      <h3 id="guide-title">${T2A.esc(title)}</h3><p id="guide-detail">${T2A.esc(detail)}</p>
      <div class="guide-progress" role="progressbar" aria-valuemin="1" aria-valuemax="5" aria-valuenow="${i + 1}"><i style="width:${(i + 1) * 20}%"></i></div>
      <div class="guide-actions"><button class="btn" id="guide-back" ${i === 0 ? "disabled" : ""}>Back</button>
      <button class="btn" id="guide-next" ${i === 4 ? "disabled" : ""}>Next</button></div>
    </aside>`;
  },
  bind() {
    document.getElementById("guide-reopen")?.addEventListener("click", () => {
      localStorage.removeItem("text2audio.guideDismissed"); Create.render();
    });
    document.getElementById("guide-skip")?.addEventListener("click", () => {
      localStorage.setItem("text2audio.guideDismissed", "1"); Create.render();
    });
    document.getElementById("guide-back")?.addEventListener("click", () => this.setStep(this.step() - 1));
    document.getElementById("guide-next")?.addEventListener("click", () => this.setStep(this.step() + 1));
  },
  setStep(step) {
    localStorage.setItem("text2audio.guideStep", String(Math.max(0, Math.min(4, step))));
    this.updateDom();
  },
  advanceTo(step) {
    if (this.step() < step) localStorage.setItem("text2audio.guideStep", String(step));
    this.updateDom();
  },
  complete() {
    localStorage.setItem("text2audio.guideStep", "4");
    localStorage.removeItem("text2audio.guideDismissed");
  },
  updateDom() {
    const title = document.getElementById("guide-title");
    if (!title) return;
    const i = T2A.state.firstValue ? 4 : this.step();
    title.textContent = GUIDE_STEPS[i][0];
    document.getElementById("guide-detail").textContent = GUIDE_STEPS[i][1];
    document.getElementById("guide-count").textContent = `BEGINNER GUIDE · ${i + 1} OF 5`;
    const progress = document.querySelector(".guide-progress");
    progress.setAttribute("aria-valuenow", String(i + 1));
    progress.querySelector("i").style.width = `${(i + 1) * 20}%`;
    document.getElementById("guide-back").disabled = i === 0;
    document.getElementById("guide-next").disabled = i === 4;
  },
};

const HELP_ITEMS = [
  ["The browser did not open", "Open http://127.0.0.1:8765 while the Text2Audio window is still running.", "browser start localhost"],
  ["ffmpeg or espeak-ng is missing", "Run the Windows installer again. Technical users can install both tools and restart Text2Audio.", "ffmpeg espeak install health"],
  ["A render looks interrupted", "Leave the app window open. Refreshing the browser reconnects to an active render on this run; after an app restart, start that render again.", "resume recover refresh job render"],
  ["Generation is slow", "CPU rendering works but can be slow. Try the safe short example first or use an NVIDIA GPU.", "slow cpu gpu performance"],
  ["A word is pronounced incorrectly", "Open Pronounce, add how the word should sound, preview it, then regenerate.", "pronounce name word dictionary"],
  ["Privacy and files", "The Studio binds to this computer only. Manuscript text, audio, voices and manifests stay in the local Text2Audio folders. UI settings and an active job ID are stored in this browser; manuscript text is not. GitHub issues are public, so never attach a private manuscript, cloned voice, or logs containing private text.", "privacy local files storage telemetry upload"],
  ["Voice cloning boundary", "Clone only your own voice, a voice you have explicit permission to use, or a public-domain recording. Do not impersonate people or make deceptive audio.", "clone consent responsible voice"],
];

const Help = {
  render() {
    const el = document.getElementById("tab-help");
    el.innerHTML = `<h2>Help</h2><p class="subtitle">Search current setup, recovery and privacy guidance.</p>
      <label class="help-search">Search help<input id="help-q" type="search" placeholder="Try: resume, ffmpeg, privacy"></label>
      <div id="help-list" class="help-list"></div><p id="help-none" class="muted" hidden>No matching help.</p>
      <div class="panel help-video"><div class="label">Captioned walkthrough</div>
        <video controls preload="metadata" poster="/help/text2audio-walkthrough-poster.png">
          <source src="/help/text2audio-walkthrough.mp4" type="video/mp4">
          <track default kind="captions" srclang="en" label="English" src="/help/text2audio-walkthrough.vtt">
        </video><p class="muted">Prefer text? The same five steps are in the beginner guide on Create.</p></div>
      <div class="help-actions"><a class="btn" target="_blank" rel="noreferrer" href="https://github.com/Nikhil-69/Text2Audio/issues/new?template=bug_report.yml">Ask for help</a>
        <a class="btn" target="_blank" rel="noreferrer" href="https://github.com/Nikhil-69/Text2Audio/issues/new?template=feature_request.yml">Request a feature</a></div>`;
    const input = document.getElementById("help-q");
    input.oninput = () => this.filter(input.value);
    this.filter("");
  },
  filter(query) {
    const words = String(query).toLowerCase().trim().split(/\s+/).filter(Boolean);
    const rows = HELP_ITEMS.filter(item => words.every(word => item.join(" ").toLowerCase().includes(word)));
    document.getElementById("help-list").innerHTML = rows.map(item =>
      `<article class="help-item"><h3>${T2A.esc(item[0])}</h3><p>${T2A.esc(item[1])}</p></article>`).join("");
    document.getElementById("help-none").hidden = rows.length !== 0;
  },
};
