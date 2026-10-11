// Shared state, API helpers, tab routing.
const T2A = {
  state: { voices: [], files: [], bookText: "", chapters: [], voice: "af_heart", speed: 0.9,
    ingestVersion: 0, ingestReady: false, firstValue: false,
    activeJob: null, activeSource: null,
    renderProgress: { percent: 0, message: "Starting…" } },
  // 4000 -> "1h 06m 40s"; drops leading zero units ("4m 05s", "12s").
  fmtDuration(totalSeconds) {
    const t = Math.max(0, Math.round(totalSeconds));
    const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
    if (h) return `${h}h ${String(m).padStart(2, "0")}m ${String(s).padStart(2, "0")}s`;
    if (m) return `${m}m ${String(s).padStart(2, "0")}s`;
    return `${s}s`;
  },

  // Escape user-controlled text before interpolating into innerHTML.
  esc(s) {
    return String(s ?? "").replace(/[&<>"']/g,
      c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  },
  async api(path, opts) {
    const r = await fetch(path, opts);
    if (!r.ok) throw new Error((await r.text()) || r.status);
    return r.headers.get("content-type")?.includes("application/json") ? r.json() : r;
  },
  toast(msg) {
    let t = document.querySelector(".toast");
    if (!t) { t = document.createElement("div"); t.className = "toast";
      t.setAttribute("role", "status"); t.setAttribute("aria-live", "polite"); document.body.appendChild(t); }
    t.textContent = msg; t.classList.add("show");
    clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("show"), 2200);
  },
  showTab(name) {
    document.querySelectorAll(".tab").forEach(b => {
      const active = b.dataset.tab === name;
      b.classList.toggle("active", active); b.setAttribute("aria-selected", String(active));
    });
    document.querySelectorAll(".tabpane").forEach(p => {
      const active = p.id === "tab-" + name;
      p.classList.toggle("active", active); p.hidden = !active;
    });
    if (name === "batch") Batch.render();
    if (name === "voices") Voices.render();
    if (name === "library") Library.render();
    if (name === "create") { Create.render(); Create.resumeActiveRender(); }
    if (name === "pronounce") Pronounce.render();
    if (name === "help") Help.render();
    location.hash = name;
  },
};

window.addEventListener("DOMContentLoaded", async () => {
  document.querySelectorAll(".tab").forEach(b => b.onclick = () => T2A.showTab(b.dataset.tab));
  document.querySelector(".tabs").onkeydown = e => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
    const tabs = [...document.querySelectorAll(".tab")];
    let index = tabs.indexOf(document.activeElement);
    if (e.key === 'Home') index = 0;
    else if (e.key === 'End') index = tabs.length - 1;
    else index = (index + (e.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
    e.preventDefault(); tabs[index].focus(); tabs[index].click();
  };
  try {
    const health = await T2A.api("/api/health");
    const missing = [!health.ffmpeg && "ffmpeg", !health.espeak && "espeak-ng",
      !health.libraryWritable && "writable library storage"].filter(Boolean);
    if (missing.length) {
      const warn = document.getElementById("ffmpeg-warn");
      warn.textContent = `⚠ ${missing.join(" and ")} not found`; warn.hidden = false;
    }
    T2A.state.voices = await T2A.api("/api/voices");
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem("text2audio.settings") || "{}"); }
    catch { localStorage.removeItem("text2audio.settings"); }
    if (saved.voice && T2A.state.voices.some(v => v.id === saved.voice)) T2A.state.voice = saved.voice;
    if (Number.isFinite(saved.speed)) T2A.state.speed = saved.speed;
    T2A.state.firstValue = (await T2A.api("/api/library")).length > 0;
  } catch (e) { T2A.toast("Backend not reachable"); }
  T2A.state.activeJob = localStorage.getItem("text2audio.activeJob");
  Create.render();
  Create.resumeActiveRender();
  if (!T2A.state.activeJob) Create.offerIncomplete();
  const initialTab = location.hash.replace(/^#/, "");
  if (["library", "create", "batch", "voices", "pronounce", "help"].includes(initialTab)) T2A.showTab(initialTab);
  else T2A.showTab("batch");
});
