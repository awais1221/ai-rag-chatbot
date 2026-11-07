// AI RAG Chatbot – main web app (chat + knowledge-base admin). No dependencies.
(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const TOKEN_KEY = "rag_admin_token";
  const state = { history: [], busy: false, token: localStorage.getItem(TOKEN_KEY) || "" };

  // ------------------------------------------------------------- markdown
  const esc = (s) => s.replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function inline(text) {
    return esc(text)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>")
      .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
        '<a href="$2" target="_blank" rel="noopener">$1</a>')
      .replace(/\[(\d{1,2})\](?!\()/g, '<button class="cite" data-n="$1" type="button">$1</button>');
  }

  // Line-by-line: paragraphs, bullet / numbered lists (also mid-paragraph), headings.
  function renderMarkdown(text) {
    const out = [];
    let para = [], items = [], listTag = "";
    const flushPara = () => { if (para.length) out.push(`<p>${para.join("<br>")}</p>`); para = []; };
    const flushList = () => { if (items.length) out.push(`<${listTag}>${items.join("")}</${listTag}>`); items = []; };
    for (const line of text.trim().split("\n")) {
      let m;
      if (!line.trim()) { flushPara(); flushList(); }
      else if ((m = line.match(/^\s*[-*•]\s+(.*)/)) || (m = line.match(/^\s*\d+[.)]\s+(.*)/))) {
        const tag = /^\s*\d/.test(line) ? "ol" : "ul";
        flushPara();
        if (tag !== listTag) flushList();
        listTag = tag;
        items.push(`<li>${inline(m[1])}</li>`);
      } else if ((m = line.match(/^#{1,6}\s+(.*)/))) {
        flushPara(); flushList();
        out.push(`<p><strong>${inline(m[1])}</strong></p>`);
      } else { flushList(); para.push(inline(line)); }
    }
    flushPara(); flushList();
    return out.join("");
  }

  // ------------------------------------------------------------- api
  async function api(path, options = {}) {
    const headers = options.headers || {};
    if (state.token) headers["X-Admin-Token"] = state.token;
    const resp = await fetch(path, { ...options, headers });
    let data = null;
    try { data = await resp.json(); } catch { /* empty body */ }
    if (!resp.ok) {
      const detail = data && data.detail;
      throw Object.assign(new Error(typeof detail === "string" ? detail : `Request failed (${resp.status})`),
        { status: resp.status });
    }
    return data;
  }

  // ------------------------------------------------------------- init
  async function init() {
    try {
      const cfg = await api("/config");
      $("#bot-name").textContent = cfg.bot_name;
      document.title = `${cfg.bot_name} – AI RAG Chatbot`;
      const pill = $("#status-pill");
      pill.textContent = cfg.llm_configured ? "AI connected" : "AI not configured";
      pill.className = "pill " + (cfg.llm_configured ? "pill-ok" : "pill-bad");
      $("#question").maxLength = cfg.max_question_chars;
      renderSuggestions(cfg.suggestions);
      if (!cfg.admin_enabled) {
        $("#kb-locked").innerHTML = '<p class="muted small">Knowledge-base editing is disabled. ' +
          "Set <code>ADMIN_TOKEN</code> in <code>.env</code> and restart to upload documents.</p>";
      } else if (state.token) {
        unlock(true);
      }
    } catch {
      $("#status-pill").textContent = "Server offline";
      $("#status-pill").className = "pill pill-bad";
    }
  }

  function renderSuggestions(list) {
    const box = $("#suggestions");
    box.innerHTML = "";
    (list || []).forEach((q) => {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = q;
      b.onclick = () => ask(q);
      box.appendChild(b);
    });
  }

  // ------------------------------------------------------------- knowledge base
  async function unlock(silent) {
    try {
      await refreshSources();
      $("#kb-locked").hidden = true;
      $("#kb-tools").hidden = false;
      $("#token-error").hidden = true;
    } catch (err) {
      state.token = "";
      localStorage.removeItem(TOKEN_KEY);
      if (!silent) {
        $("#token-error").textContent = err.message;
        $("#token-error").hidden = false;
      }
    }
  }

  $("#token-form").addEventListener("submit", (e) => {
    e.preventDefault();
    state.token = $("#token-input").value.trim();
    localStorage.setItem(TOKEN_KEY, state.token);
    unlock(false);
  });

  $("#lock-btn").onclick = () => {
    state.token = "";
    localStorage.removeItem(TOKEN_KEY);
    $("#kb-tools").hidden = true;
    $("#kb-locked").hidden = false;
    $("#kb-count").textContent = "";
  };

  const ICONS = { url: "🌐", pdf: "📕", docx: "📘", text: "📄" };

  async function refreshSources() {
    const sources = await api("/sources");
    const list = $("#source-list");
    list.innerHTML = "";
    const chunks = sources.reduce((n, s) => n + s.chunks, 0);
    $("#kb-count").textContent = sources.length
      ? `${sources.length} source${sources.length > 1 ? "s" : ""} · ${chunks} chunks` : "empty";
    if (!sources.length) {
      list.innerHTML = '<li class="muted">No documents yet. Upload files or index a website.</li>';
    }
    sources.forEach((s) => {
      const li = document.createElement("li");
      li.innerHTML = `<span class="src-icon">${ICONS[s.source_type] || "📄"}</span>
        <span class="src-main"><span class="src-title"></span><span class="src-meta"></span></span>
        <button class="del" type="button" title="Remove">✕</button>`;
      li.querySelector(".src-title").textContent = s.title;
      li.querySelector(".src-meta").textContent =
        `${s.chunks} chunk${s.chunks > 1 ? "s" : ""}` + (s.source_type === "url" ? ` · ${s.source}` : "");
      li.querySelector(".del").onclick = async () => {
        if (!confirm(`Remove "${s.title}" from the knowledge base?`)) return;
        await api(`/sources?source=${encodeURIComponent(s.source)}`, { method: "DELETE" });
        refreshSources();
      };
      list.appendChild(li);
    });
    return sources;
  }

  function showStatus(kind, html) {
    const box = $("#ingest-status");
    box.className = "ingest-status " + (kind || "");
    box.innerHTML = html;
    box.hidden = false;
  }

  function reportIngest(data) {
    const ok = data.results.filter((r) => r.status === "success");
    const bad = data.results.filter((r) => r.status === "error");
    let html = `Indexed <strong>${ok.length}</strong> source${ok.length === 1 ? "" : "s"} (${data.total_chunks} chunks).`;
    if (bad.length) {
      html += `<ul>${bad.slice(0, 5).map((r) => `<li>${esc(r.title)}: ${esc(r.error || "")}</li>`).join("")}</ul>`;
      if (bad.length > 5) html += `…and ${bad.length - 5} more failed.`;
    }
    showStatus(bad.length && !ok.length ? "bad" : "ok", html);
    refreshSources();
  }

  async function uploadFiles(files) {
    if (!files.length) return;
    const form = new FormData();
    [...files].forEach((f) => form.append("files", f));
    showStatus("", `Reading and indexing ${files.length} file${files.length > 1 ? "s" : ""}…`);
    try {
      reportIngest(await api("/ingest/files", { method: "POST", body: form }));
    } catch (err) {
      showStatus("bad", esc(err.message));
    }
  }

  const dz = $("#dropzone");
  const fileInput = $("#file-input");
  dz.onclick = () => fileInput.click();
  dz.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } };
  fileInput.onchange = () => { uploadFiles(fileInput.files); fileInput.value = ""; };
  ["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("dragover"); }));
  ["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("dragover"); }));
  dz.addEventListener("drop", (e) => uploadFiles(e.dataTransfer.files));

  $("#url-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = e.submitter;
    btn.disabled = true;
    showStatus("", "Fetching pages and indexing… (a whole site can take a minute)");
    try {
      const body = JSON.stringify({ url: $("#url-input").value.trim(), mode: $("#url-mode").value });
      reportIngest(await api("/ingest/url", { method: "POST", body, headers: { "Content-Type": "application/json" } }));
    } catch (err) {
      showStatus("bad", esc(err.message));
    } finally {
      btn.disabled = false;
    }
  });

  $("#reset-btn").onclick = async () => {
    if (!confirm("Delete ALL documents from the knowledge base?")) return;
    await api("/reset", { method: "POST" });
    $("#ingest-status").hidden = true;
    refreshSources();
  };

  // ------------------------------------------------------------- chat
  const messages = $("#messages");
  const scrollDown = () => { messages.scrollTop = messages.scrollHeight; };

  function addMessage(role, html) {
    $("#empty-state").hidden = true;
    const el = document.createElement("div");
    el.className = `msg ${role}`;
    el.innerHTML = `<div class="avatar">${role === "user" ? "🙂" : "🤖"}</div><div class="content"><div class="bubble">${html}</div></div>`;
    messages.appendChild(el);
    scrollDown();
    return el;
  }

  function renderSources(container, sources, cited) {
    const shown = sources.filter((s) => (cited || []).includes(s.index));
    if (!shown.length) return;
    const box = document.createElement("div");
    box.className = "sources";
    shown.forEach((s) => {
      const card = document.createElement(s.url ? "a" : "div");
      card.className = "source-card";
      card.dataset.n = s.index;
      if (s.url) { card.href = s.url; card.target = "_blank"; card.rel = "noopener"; }
      card.innerHTML = `<div class="sc-title"><span class="sc-num">${s.index}</span><span></span></div><div class="sc-snippet"></div>`;
      card.querySelector(".sc-title span:last-child").textContent = s.title;
      card.querySelector(".sc-snippet").textContent = s.snippet;
      box.appendChild(card);
    });
    container.appendChild(box);
  }

  messages.addEventListener("click", (e) => {
    const cite = e.target.closest(".cite");
    if (!cite) return;
    const card = cite.closest(".msg").querySelector(`.source-card[data-n="${cite.dataset.n}"]`);
    if (!card) return;
    card.scrollIntoView({ behavior: "smooth", block: "nearest" });
    card.classList.add("flash");
    setTimeout(() => card.classList.remove("flash"), 1200);
  });

  async function ask(question) {
    question = question.trim();
    if (!question || state.busy) return;
    state.busy = true;
    $("#send-btn").disabled = true;
    addMessage("user", esc(question).replace(/\n/g, "<br>"));
    const botEl = addMessage("bot", '<span class="typing"><span></span><span></span><span></span></span>');
    const bubble = botEl.querySelector(".bubble");
    let text = "";
    let sources = [];
    let failed = false;

    try {
      const resp = await fetch("/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, history: state.history }),
      });
      if (!resp.ok) {
        let detail = `Request failed (${resp.status})`;
        try { detail = (await resp.json()).detail || detail; } catch { /* ignore */ }
        throw new Error(detail);
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buffer.indexOf("\n\n")) >= 0) {
          const line = buffer.slice(0, idx).trim();
          buffer = buffer.slice(idx + 2);
          if (!line.startsWith("data: ")) continue;
          const ev = JSON.parse(line.slice(6));
          if (ev.type === "sources") sources = ev.sources;
          else if (ev.type === "token") { text += ev.text; bubble.innerHTML = renderMarkdown(text); scrollDown(); }
          else if (ev.type === "error") throw new Error(ev.message);
          else if (ev.type === "done") renderSources(botEl.querySelector(".content"), sources, ev.cited);
        }
      }
    } catch (err) {
      failed = true;
      bubble.classList.add("error");
      bubble.textContent = err.message || "Something went wrong.";
    } finally {
      state.busy = false;
      $("#send-btn").disabled = false;
      scrollDown();
    }
    if (!failed && text) {
      state.history.push({ role: "user", content: question }, { role: "assistant", content: text });
      state.history = state.history.slice(-12);
    }
  }

  const input = $("#question");
  $("#chat-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const q = input.value;
    input.value = "";
    input.style.height = "auto";
    ask(q);
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#chat-form").requestSubmit(); }
  });
  input.addEventListener("input", () => { input.style.height = "auto"; input.style.height = input.scrollHeight + "px"; });

  $("#clear-chat").onclick = () => {
    state.history = [];
    messages.querySelectorAll(".msg").forEach((m) => m.remove());
    $("#empty-state").hidden = false;
  };

  init();
})();
