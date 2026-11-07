/*!
 * AI RAG Chatbot – embeddable chat widget. No dependencies, ~10 KB.
 *
 * Add one line to any website:
 *   <script src="https://YOUR-SERVER/widget.js" defer
 *           data-title="Site Assistant" data-color="#3758f9"
 *           data-greeting="Hi! Ask me anything about this site."></script>
 *
 * Options (all optional): data-title, data-color, data-greeting,
 * data-position ("right" | "left"), data-api (server URL; defaults to the
 * server the script was loaded from).
 *
 * Styles live in a Shadow DOM, so the host page's CSS cannot break the widget
 * and the widget cannot break the host page.
 */
(() => {
  "use strict";
  if (window.__ragWidgetLoaded) return;
  window.__ragWidgetLoaded = true;

  const script = document.currentScript || document.querySelector('script[src*="widget.js"]');
  const ds = (script && script.dataset) || {};
  const API = (ds.api || new URL(script.src, location.href).origin).replace(/\/$/, "");
  const COLOR = /^#[0-9a-f]{3,8}$/i.test(ds.color || "") ? ds.color : "#3758f9";
  const SIDE = ds.position === "left" ? "left" : "right";
  const STORE_KEY = "rag_widget_chat";

  let title = ds.title || "Site Assistant";
  let greeting = ds.greeting || "Hi! 👋 Ask me anything about this site.";
  let suggestions = [];
  let configLoaded = false;
  let busy = false;
  let saved = { history: [], log: [] };
  try { saved = JSON.parse(sessionStorage.getItem(STORE_KEY)) || saved; } catch { /* ignore */ }

  // ---------------------------------------------------------------- markup
  const host = document.createElement("div");
  host.id = "rag-chat-widget";
  const root = host.attachShadow({ mode: "open" });
  root.innerHTML = `
<style>
  :host { all: initial; }
  * { box-sizing: border-box; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
  .launcher { position: fixed; bottom: 20px; ${SIDE}: 20px; width: 58px; height: 58px; border-radius: 50%;
    background: ${COLOR}; color: #fff; border: 0; cursor: pointer; box-shadow: 0 6px 20px rgba(0,0,0,.25);
    display: grid; place-items: center; z-index: 2147483000; transition: transform .2s; }
  .launcher:hover { transform: scale(1.06); }
  .launcher svg { width: 26px; height: 26px; }
  .panel { position: fixed; bottom: 90px; ${SIDE}: 20px; width: 370px; max-width: calc(100vw - 24px);
    height: 560px; max-height: calc(100vh - 110px); background: #fff; border-radius: 16px;
    box-shadow: 0 12px 40px rgba(0,0,0,.22); display: flex; flex-direction: column; overflow: hidden;
    z-index: 2147483000; color: #1d2433; font-size: 14px; line-height: 1.5;
    opacity: 0; transform: translateY(12px) scale(.98); pointer-events: none; transition: .18s ease; }
  .panel.open { opacity: 1; transform: none; pointer-events: auto; }
  @media (max-width: 480px) { .panel { bottom: 0; ${SIDE}: 0; width: 100vw; max-width: 100vw; height: 100%; max-height: 100%; border-radius: 0; } }
  header { background: ${COLOR}; color: #fff; padding: 14px 16px; display: flex; align-items: center; gap: 10px; }
  header .t { flex: 1; font-weight: 700; font-size: 15px; }
  header .s { font-size: 12px; opacity: .85; font-weight: 400; display: block; }
  header button { background: rgba(255,255,255,.15); border: 0; color: #fff; width: 30px; height: 30px; border-radius: 8px; cursor: pointer; font-size: 16px; }
  header button:hover { background: rgba(255,255,255,.28); }
  .msgs { flex: 1; overflow-y: auto; padding: 14px; display: flex; flex-direction: column; gap: 10px; background: #f7f8fb; }
  .m { max-width: 88%; padding: 9px 13px; border-radius: 14px; overflow-wrap: anywhere; }
  .m p { margin: 0 0 6px; } .m p:last-child { margin: 0; }
  .m ul, .m ol { margin: 2px 0 6px; padding-left: 18px; }
  .m code { background: rgba(0,0,0,.06); padding: 0 4px; border-radius: 4px; }
  .m a { color: ${COLOR}; }
  .bot { background: #fff; border: 1px solid #e6e9f0; align-self: flex-start; border-top-left-radius: 4px; }
  .user { background: ${COLOR}; color: #fff; align-self: flex-end; border-top-right-radius: 4px; }
  .err { background: #fdecea; color: #b42318; align-self: flex-start; }
  .note { align-self: center; font-size: 12px; color: #6b7385; }
  .m .cite { color: #fff; }
  .cite { display: inline-block; min-width: 16px; padding: 0 4px; border-radius: 4px; background: ${COLOR}; color: #fff;
    font-size: 10px; font-weight: 700; text-align: center; vertical-align: 2px; text-decoration: none; }
  .srcs { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 8px; }
  .srcs a, .srcs span { font-size: 12px; border: 1px solid #e3e7ef; border-radius: 999px; padding: 2px 9px; color: #3a4256;
    text-decoration: none; background: #fafbfd; max-width: 100%; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .srcs a:hover { border-color: ${COLOR}; color: ${COLOR}; }
  .sugg { display: flex; flex-wrap: wrap; gap: 6px; }
  .sugg button { border: 1px solid ${COLOR}; color: ${COLOR}; background: #fff; border-radius: 999px; padding: 5px 11px; font-size: 12.5px; cursor: pointer; }
  .sugg button:hover { background: ${COLOR}; color: #fff; }
  .dots span { display: inline-block; width: 6px; height: 6px; margin: 0 2px; border-radius: 50%; background: #9aa3b5; animation: b 1.2s infinite; }
  .dots span:nth-child(2) { animation-delay: .2s; } .dots span:nth-child(3) { animation-delay: .4s; }
  @keyframes b { 0%, 80%, 100% { opacity: .25; } 40% { opacity: 1; } }
  form { display: flex; gap: 8px; padding: 10px; border-top: 1px solid #e6e9f0; background: #fff; }
  textarea { flex: 1; resize: none; border: 1px solid #dfe3ec; border-radius: 10px; padding: 9px 11px; font-size: 14px; max-height: 110px; outline: none; color: #1d2433; }
  textarea:focus { border-color: ${COLOR}; }
  form button { background: ${COLOR}; border: 0; color: #fff; border-radius: 10px; width: 42px; cursor: pointer; display: grid; place-items: center; }
  form button:disabled { opacity: .5; cursor: not-allowed; }
  .foot { text-align: center; font-size: 11px; color: #9aa3b5; padding: 0 0 8px; background: #fff; }
</style>
<button class="launcher" aria-label="Open chat">
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
</button>
<div class="panel" role="dialog" aria-label="Chat">
  <header>
    <div class="t"><span class="name"></span><span class="s">AI answers from this site's content</span></div>
    <button class="reset" title="New chat" aria-label="New chat">↺</button>
    <button class="close" title="Close" aria-label="Close chat">✕</button>
  </header>
  <div class="msgs" aria-live="polite"></div>
  <form>
    <textarea rows="1" placeholder="Type your question…" maxlength="500" aria-label="Your question"></textarea>
    <button type="submit" aria-label="Send"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2 11 13"/><path d="M22 2 15 22l-4-9-9-4 20-7z"/></svg></button>
  </form>
  <div class="foot">AI can make mistakes. Check the linked sources.</div>
</div>`;

  const $ = (s) => root.querySelector(s);
  const panel = $(".panel");
  const msgs = $(".msgs");
  const input = $("textarea");
  const sendBtn = $("form button");
  $(".name").textContent = title;

  // ---------------------------------------------------------------- helpers
  const esc = (s) => s.replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function inline(t, sources) {
    return esc(t)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
      .replace(/\[(\d{1,2})\](?!\()/g, (m, n) => {
        const s = (sources || []).find((x) => String(x.index) === n);
        return s && s.url ? `<a class="cite" href="${esc(s.url)}" target="_blank" rel="noopener">${n}</a>`
                          : `<span class="cite">${n}</span>`;
      });
  }

  // Line-by-line: paragraphs, bullet / numbered lists (also mid-paragraph), headings.
  function md(text, sources) {
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
        items.push(`<li>${inline(m[1], sources)}</li>`);
      } else if ((m = line.match(/^#{1,6}\s+(.*)/))) {
        flushPara(); flushList();
        out.push(`<p><strong>${inline(m[1], sources)}</strong></p>`);
      } else { flushList(); para.push(inline(line, sources)); }
    }
    flushPara(); flushList();
    return out.join("");
  }

  function bubble(cls, html) {
    const el = document.createElement("div");
    el.className = "m " + cls;
    el.innerHTML = html;
    msgs.appendChild(el);
    msgs.scrollTop = msgs.scrollHeight;
    return el;
  }

  function sourceChips(el, sources, cited) {
    const shown = sources.filter((s) => (cited || []).includes(s.index));
    if (!shown.length) return;
    const box = document.createElement("div");
    box.className = "srcs";
    shown.forEach((s) => {
      const chip = document.createElement(s.url ? "a" : "span");
      chip.textContent = `${s.index}. ${s.title.replace(/\s[|–-]\s.*$/, "")}`;
      chip.title = s.snippet;
      if (s.url) { chip.href = s.url; chip.target = "_blank"; chip.rel = "noopener"; }
      box.appendChild(chip);
    });
    el.appendChild(box);
  }

  function persist() {
    try { sessionStorage.setItem(STORE_KEY, JSON.stringify(saved)); } catch { /* storage full/blocked */ }
  }

  function showSuggestions() {
    if (!suggestions.length || saved.log.length) return;
    const box = document.createElement("div");
    box.className = "sugg";
    suggestions.slice(0, 4).forEach((q) => {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = q;
      b.onclick = () => { box.remove(); ask(q); };
      box.appendChild(b);
    });
    msgs.appendChild(box);
  }

  function renderLog() {
    msgs.innerHTML = "";
    bubble("bot", md(greeting));
    saved.log.forEach((item) => {
      if (item.role === "user") bubble("user", esc(item.text));
      else { const el = bubble("bot", md(item.text, item.sources)); sourceChips(el, item.sources, item.cited); }
    });
    showSuggestions();
  }

  // ---------------------------------------------------------------- config (also wakes the server)
  async function loadConfig() {
    if (configLoaded) return;
    const slow = setTimeout(() => {
      if (!configLoaded) bubble("note", "Waking up the assistant… this can take up to a minute the first time.");
    }, 2500);
    try {
      const cfg = await (await fetch(API + "/config")).json();
      configLoaded = true;
      if (!ds.title && cfg.bot_name) { title = cfg.bot_name; $(".name").textContent = title; }
      suggestions = cfg.suggestions || [];
      if (cfg.max_question_chars) input.maxLength = cfg.max_question_chars;
      renderLog();
    } catch {
      bubble("err", "The assistant is offline right now. Please try again later.");
    } finally {
      clearTimeout(slow);
    }
  }

  // ---------------------------------------------------------------- chat
  async function ask(question) {
    question = question.trim();
    if (!question || busy) return;
    busy = true;
    sendBtn.disabled = true;
    root.querySelectorAll(".sugg").forEach((s) => s.remove());
    bubble("user", esc(question));
    const el = bubble("bot", '<span class="dots"><span></span><span></span><span></span></span>');
    let text = "", sources = [], cited = [];

    try {
      const resp = await fetch(API + "/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, history: saved.history }),
      });
      if (!resp.ok) {
        let detail = "Sorry, something went wrong. Please try again.";
        try { detail = (await resp.json()).detail || detail; } catch { /* ignore */ }
        throw new Error(detail);
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf("\n\n")) >= 0) {
          const line = buf.slice(0, i).trim();
          buf = buf.slice(i + 2);
          if (!line.startsWith("data: ")) continue;
          const ev = JSON.parse(line.slice(6));
          if (ev.type === "sources") sources = ev.sources;
          else if (ev.type === "token") { text += ev.text; el.innerHTML = md(text, sources); msgs.scrollTop = msgs.scrollHeight; }
          else if (ev.type === "error") throw new Error(ev.message);
          else if (ev.type === "done") { cited = ev.cited || []; sourceChips(el, sources, cited); }
        }
      }
      saved.log.push({ role: "user", text: question },
                     { role: "bot", text, sources: sources.filter((s) => cited.includes(s.index)), cited });
      saved.history.push({ role: "user", content: question }, { role: "assistant", content: text });
      saved.history = saved.history.slice(-12);
      saved.log = saved.log.slice(-30);
      persist();
    } catch (err) {
      el.className = "m err";
      el.textContent = err.message;
    } finally {
      busy = false;
      sendBtn.disabled = false;
      msgs.scrollTop = msgs.scrollHeight;
    }
  }

  // ---------------------------------------------------------------- events
  function toggle(open) {
    panel.classList.toggle("open", open);
    $(".launcher").setAttribute("aria-label", open ? "Close chat" : "Open chat");
    if (open) { loadConfig(); setTimeout(() => input.focus(), 150); }
  }
  $(".launcher").onclick = () => toggle(!panel.classList.contains("open"));
  $(".close").onclick = () => toggle(false);
  $(".reset").onclick = () => { saved = { history: [], log: [] }; persist(); renderLog(); };
  $("form").addEventListener("submit", (e) => {
    e.preventDefault();
    const q = input.value;
    input.value = "";
    input.style.height = "auto";
    ask(q);
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("form").requestSubmit(); }
    if (e.key === "Escape") toggle(false);
  });
  input.addEventListener("input", () => { input.style.height = "auto"; input.style.height = input.scrollHeight + "px"; });

  const mount = () => document.body.appendChild(host);
  if (document.body) mount(); else document.addEventListener("DOMContentLoaded", mount);
})();
