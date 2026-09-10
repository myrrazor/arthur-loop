"use strict";
/* Arthur Loop web console — vanilla, no build step.
   Read-mostly control surface: polls arthur status, renders five views, and
   offers a human exactly five write actions (answer decision, recover job,
   create job, clear session, break stale lock). Everything else is agent-owned. */

const TOKEN = document.querySelector('meta[name="arthur-token"]').content;
const POLL_MS = 3000;
// the bootstrap URL carries the session token; keep it out of the address bar / history
if (location.search.includes("token=")) history.replaceState(null, "", location.pathname);

const STATE_META = {
  WAIT: { color: "var(--wait)", verb: "Resting", hint: "nothing due — the loop is idle" },
  POLL_DUE: { color: "var(--go)", verb: "Poll", hint: "queue work is ready to submit or poll" },
  BLOCKED_BY_BROWSER_LOCK: { color: "var(--lock)", verb: "Waiting on lock", hint: "due work is blocked on the browser lock" },
  BLOCKED_BY_QUOTA: { color: "var(--quota)", verb: "Paused", hint: "quota at reserve — checkpoint and wait" },
  HUMAN_INPUT_REQUIRED: { color: "var(--human)", verb: "Answer", hint: "a human decision is the only thing moving this forward" },
};
const JOB_STATE = {
  queued: "var(--flow)", claimed: "var(--lock)", submitted: "var(--lock)",
  waiting_for_chatgpt: "var(--flow)", stopped_no_output: "var(--quota)",
  needs_recovery: "var(--quota)", completed: "var(--wait)",
  completed_with_warnings: "var(--lock)", failed: "var(--quota)", cancelled: "var(--wait)",
};
// "claimed" counts as in flight: the queue manager owns it and is about to submit
const INFLIGHT = new Set(["claimed", "submitted", "waiting_for_chatgpt", "stopped_no_output"]);
const TERMINAL = new Set(["completed", "completed_with_warnings", "failed", "cancelled"]);
const VISIT_KEY = "arthur:lastVisit";

const store = {
  status: null, events: [], eventsLoaded: false, view: "canvas", project: null,
  lastOk: 0, failing: false,
  railSig: "", viewSig: {},
  prevVisit: Number(localStorage.getItem(VISIT_KEY) || 0),
};
localStorage.setItem(VISIT_KEY, String(Date.now()));

const $ = (sel, root = document) => root.querySelector(sel);
const bind = (name) => document.querySelector(`[data-bind="${name}"]`);
const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };

/* ---- time helpers ------------------------------------------------------ */

function parseAt(v) { if (!v) return null; const d = new Date(v); return isNaN(d) ? null : d; }
function rel(v, now = Date.now()) {
  const d = parseAt(v); if (!d) return "—";
  const s = (d.getTime() - now) / 1000; const a = Math.abs(s);
  if (a < 45) return "now";
  const span = a < 3600 ? `${Math.round(a / 60)}m` : a < 86400 ? `${Math.round(a / 3600)}h` : `${Math.round(a / 86400)}d`;
  return s > 0 ? `in ${span}` : `${span} ago`;
}

/* ---- networking -------------------------------------------------------- */

const AUTH_HEADERS = { "Cache-Control": "no-store", "X-Arthur-Token": TOKEN };
async function getJSON(path) {
  const r = await fetch(path, { headers: AUTH_HEADERS });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || `HTTP ${r.status}`);
  return r.json();
}
async function getText(path) {
  const r = await fetch(path, { headers: AUTH_HEADERS });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || `HTTP ${r.status}`);
  return r.text();
}
async function action(path, body) {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Arthur-Token": TOKEN },
    body: JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}

let pollBusy = false;
async function poll() {
  if (pollBusy) return; // no overlapping polls: stale responses must not win
  pollBusy = true;
  try {
    const status = await getJSON("/api/status");
    store.status = status;
    store.lastOk = Date.now();
    store.failing = false;
    if (!store.project && status.projects.length) store.project = status.projects[0].projectId;
    if (store.view === "events") {
      try {
        store.events = await getJSON("/api/events?n=150");
        store.eventsLoaded = true;
      } catch (_) { /* keep the old list; status itself succeeded */ }
    }
    render();
  } catch (err) {
    store.failing = true;
    if (!store.status) {
      bind("skeleton").replaceChildren(
        el("div", "rail-empty", "Can't reach the console server — is `arthur web` still running? Retrying…")
      );
    }
    updateFreshness();
  } finally {
    pollBusy = false;
  }
}

/* ---- derived: the next-action headline --------------------------------- */

function nextAction(status) {
  const meta = STATE_META[status.state] || STATE_META.WAIT;
  const now = Date.now();
  if (status.state === "HUMAN_INPUT_REQUIRED" && status.decisions.length) {
    return { verb: "Answer", target: status.decisions[0].title, color: meta.color };
  }
  if (status.state === "POLL_DUE" && status.tick.dueJobId) {
    const job = status.queue.find((j) => j.jobId === status.tick.dueJobId);
    const eta = job && job.status !== "queued" ? rel(job.nextPollAt, now) : "ready";
    return { verb: "Poll", target: `${status.tick.dueJobId} · ${eta}`, color: meta.color };
  }
  if (status.state === "WAIT" && status.tick.nextDueAt) {
    return { verb: "Idle", target: `next due ${rel(status.tick.nextDueAt, now)}`, color: meta.color };
  }
  return { verb: meta.verb, target: meta.hint, color: meta.color };
}

/* ---- top render -------------------------------------------------------- */

function render() {
  const s = store.status;
  if (!s) return;
  document.getElementById("app").setAttribute("aria-busy", "false");
  bind("skeleton").hidden = true;

  bind("instance").textContent = s.server.instance;
  bind("adapters").textContent = `advisor: ${s.server.advisor}\nexecutor: ${s.server.executor}`;

  const chip = bind("state-chip");
  chip.dataset.state = s.state;
  bind("state-label").textContent = s.state.replace(/_/g, " ");
  const na = nextAction(s);
  bind("state-hint").textContent = `${na.verb} · ${na.target}`;

  renderQuotaMini(s);
  renderLock(s);
  renderProjects(s);
  renderRail(s);
  updateFreshness();

  const qCount = s.queue.length;
  const qc = bind("count-queue");
  qc.hidden = qCount === 0; qc.textContent = qCount;

  renderView();
}

function updateFreshness() {
  const node = bind("freshness");
  const txt = bind("freshness-text");
  if (store.failing) { node.classList.add("stale"); txt.textContent = "reconnecting"; return; }
  node.classList.remove("stale");
  txt.textContent = "live";
}

function renderLock(s) {
  const wrap = bind("lock-chip");
  const lock = s.browserLock;
  if (!lock) { wrap.hidden = true; return; }
  wrap.hidden = false;
  wrap.classList.toggle("is-stale", !lock.fresh);
  bind("lock-text").textContent = lock.fresh ? `lock: ${lock.holder}` : `stale lock: ${lock.holder}`;
  const btn = bind("lock-break");
  btn.hidden = lock.fresh; // breaking a fresh lock would yank it from an active agent
  btn.onclick = async () => {
    btn.disabled = true;
    try { await action("/api/actions/break-lock", {}); toast("ok", "Stale lock broken", lock.holder); poll(); }
    catch (e) { toast("err", "Could not break lock", e.message); }
    finally { btn.disabled = false; }
  };
}

function renderQuotaMini(s) {
  const wrap = bind("quota-mini");
  if (!s.quota || s.quota.leftPercent == null) { wrap.hidden = true; return; }
  wrap.hidden = false;
  const left = s.quota.leftPercent;
  const color = { GREEN: "var(--go)", YELLOW: "var(--lock)", RED: "var(--quota)" }[s.quota.state] || "var(--wait)";
  const fill = bind("quota-mini-fill");
  fill.style.width = `${Math.max(0, Math.min(100, left))}%`;
  fill.style.background = color;
  bind("quota-mini-value").textContent = `${Math.round(left)}%`;
}

function renderProjects(s) {
  const list = bind("project-list");
  list.replaceChildren();
  if (!s.projects.length) { list.append(el("div", "rail-empty", "no projects yet")); return; }
  for (const p of s.projects) {
    const jobs = s.queue.filter((j) => j.projectId === p.projectId).length;
    const row = el("button", `project-chip${p.blockedByDecision ? " blocked" : ""}`);
    row.append(el("i", "pdot"), el("span", "pname", p.projectId));
    if (jobs) row.append(el("span", "pcount", String(jobs)));
    row.title = p.summary;
    row.onclick = () => { store.project = p.projectId; setView("artifacts"); };
    list.append(row);
  }
}

/* ---- right rail: needs you --------------------------------------------- */

function railSignature(s) {
  return JSON.stringify([
    s.decisions,
    s.tick.staleJobIds,
    s.sessions.map((x) => [x.sessionId, x.state, x.activity, x.stale, x.projectId]),
    (s.quarantine || []).map((q) => q.path),
  ]);
}

function renderRail(s) {
  const body = bind("rail-body");
  const quarantine = s.quarantine || [];
  const attention = s.decisions.length + (s.tick.staleJobIds ? s.tick.staleJobIds.length : 0) + quarantine.length;
  const badge = bind("attention-badge");
  badge.hidden = attention === 0; badge.textContent = attention;

  // never nuke a half-typed answer: if the human is mid-draft, or nothing
  // changed, keep the DOM and just refresh the relative timestamps
  const sig = railSignature(s);
  const typing = body.contains(document.activeElement) && document.activeElement.tagName === "TEXTAREA";
  const hasDraft = [...body.querySelectorAll("textarea")].some((t) => t.value.trim());
  if (sig === store.railSig || typing || hasDraft) {
    body.querySelectorAll("[data-seen-at]").forEach((n) => { n.textContent = rel(n.dataset.seenAt); });
    return;
  }
  store.railSig = sig;
  body.replaceChildren();

  if (s.decisions.length) {
    body.append(el("div", "rail-group-title", "Open decisions"));
    for (const d of s.decisions) body.append(decisionCard(d));
  }

  const staleIds = new Set(s.tick.staleJobIds || []);
  const staleJobs = s.queue.filter((j) => staleIds.has(j.jobId));
  if (staleJobs.length) {
    body.append(el("div", "rail-group-title", "Stale jobs"));
    for (const j of staleJobs) body.append(staleCard(j));
  }

  if (quarantine.length) {
    body.append(el("div", "rail-group-title", "Quarantined artifacts"));
    for (const q of quarantine) body.append(quarantineCard(q));
  }

  body.append(el("div", "rail-group-title", "Sessions"));
  if (!s.sessions.length) {
    body.append(el("div", "rail-empty", "no sessions reporting — agents appear here when they report in"));
  } else {
    for (const sess of s.sessions) body.append(sessionRow(sess));
  }

  if (attention === 0) {
    const calm = el("div", "rail-empty");
    const line = el("span", "calm"); line.append(el("i", "dot"), document.createTextNode("Nothing needs you right now."));
    line.querySelector("i").style.cssText = "width:7px;height:7px;border-radius:50%;background:var(--go)";
    calm.append(line);
    body.prepend(calm);
  }
}

function quarantineCard(q) {
  const card = el("div", "quarantine");
  card.append(el("div", "q-kind", `${q.projectId} · ${q.kind}`));
  if (q.reasons) card.append(el("div", "q-why", q.reasons));
  card.append(el("div", "q-note", "Control block failed validation. Do not act on this artifact; a human should look."));
  const actions = el("div", "d-actions");
  const inspect = el("button", "btn ghost sm", "Inspect");
  inspect.onclick = () => openFile(q.path, `${q.projectId} quarantined ${q.kind}`);
  actions.append(inspect);
  card.append(actions);
  return card;
}

function decisionCard(d) {
  const card = el("div", "decision");
  card.append(el("div", "d-title", d.title), el("div", "d-project", d.projectId || ""));
  if (d.body) card.append(el("div", "d-body", d.body));
  const ta = el("textarea"); ta.placeholder = "Answer this decision. It records into human-decisions/open.md and unblocks the project.";
  card.append(ta);
  const actions = el("div", "d-actions");
  const submit = el("button", "btn human sm", "Answer & unblock");
  const view = el("button", "btn ghost sm", "View");
  submit.onclick = async () => {
    const answer = ta.value.trim();
    if (!answer) { ta.focus(); return; }
    submit.disabled = true;
    try {
      await action("/api/actions/answer-decision", { title: d.title, answer });
      ta.value = ""; // clear the draft so the rail is free to rebuild
      toast("ok", "Decision answered", d.title);
      poll();
    } catch (e) { toast("err", "Could not answer", e.message); submit.disabled = false; }
  };
  view.onclick = () => openFile("human-decisions/open.md", d.title);
  actions.append(submit, view);
  card.append(actions);
  return card;
}

function staleCard(j) {
  const card = el("div", "stale-job");
  card.append(el("div", "s-id", j.jobId), el("div", "s-meta", `${j.projectId} · ${j.status} · attempt ${j.attemptCount}`));
  const actions = el("div", "s-actions");
  const requeue = el("button", "btn sm", "Recover & requeue");
  const park = el("button", "btn ghost sm", "Park");
  requeue.onclick = () => runRecover(j.jobId, true, requeue);
  park.onclick = () => runRecover(j.jobId, false, park);
  actions.append(requeue, park);
  card.append(actions);
  return card;
}

async function runRecover(jobId, requeue, btn) {
  btn.disabled = true;
  try { await action("/api/actions/recover-job", { job_id: jobId, requeue }); toast("ok", requeue ? "Requeued" : "Parked", jobId); poll(); }
  catch (e) { toast("err", "Recover failed", e.message); btn.disabled = false; }
}

function sessionRow(sess) {
  const row = el("div", `session-row ${sess.state}${sess.stale ? " stale" : ""}`);
  row.append(el("i", "sdot"));
  const mid = el("div"); mid.style.cssText = "min-width:0;flex:1";
  mid.append(el("div", "s-name", sess.projectId ? `${sess.sessionId} · ${sess.projectId}` : sess.sessionId));
  mid.append(el("div", "s-act", sess.activity || sess.role));
  row.append(mid);
  const seen = el("span", "s-seen", rel(sess.at));
  seen.dataset.seenAt = sess.at;
  row.append(seen);
  const clear = el("button", "btn ghost sm s-clear", "clear");
  clear.title = "Drop this session from the dashboard";
  clear.onclick = async () => { try { await action("/api/actions/clear-session", { session_id: sess.sessionId }); poll(); } catch (e) { toast("err", "Clear failed", e.message); } };
  row.append(clear);
  return row;
}

/* ---- view routing ------------------------------------------------------ */

function setView(name) { store.view = name; syncNav(); renderView(); if (name === "events") poll(); }
function syncNav() {
  document.querySelectorAll(".railnav-item").forEach((b) => b.classList.toggle("is-active", b.dataset.view === store.view));
  document.querySelectorAll("[data-view-panel]").forEach((p) => { p.hidden = p.dataset.viewPanel !== store.view; });
}
function renderView() {
  syncNav();
  const s = store.status; if (!s) return;
  const panel = $(`[data-view-panel="${store.view}"]`);
  if (store.view === "canvas") return renderCanvas(panel, s);
  // rebuild-views skip re-render when their data is unchanged, so scroll
  // position and artifact selection survive the 3s poll
  const sig = viewSignature(store.view, s);
  if (sig && sig === store.viewSig[store.view]) return;
  store.viewSig[store.view] = sig;
  if (store.view === "board") return renderBoard(panel, s);
  if (store.view === "queue") return renderQueue(panel, s);
  if (store.view === "artifacts") return renderArtifacts(panel, s);
  if (store.view === "events") return renderEvents(panel, s);
}

function viewSignature(view, s) {
  // the 30s bucket lets relative "next poll" times refresh without a data change
  const bucket = Math.floor(Date.now() / 30000);
  if (view === "board" || view === "queue") return JSON.stringify([s.queue, s.hiddenTerminalJobs, bucket]);
  if (view === "events") return store.events.length ? `${store.events[0].at}:${store.events.length}` : (store.eventsLoaded ? "empty" : "loading");
  if (view === "artifacts") return `artifacts:${store.project || ""}`;
  return "";
}

/* ---- the loop canvas (signature view) ---------------------------------- */
/* Fixed hand-authored topology; the whole graph pans and zooms. Work is
   rendered as counts and flow on a static map, never one node per job. */

const NODES = [
  { id: "advisor", x: 60, y: 150, label: "ADVISOR" },
  { id: "queue", x: 300, y: 150, label: "QUEUE" },
  { id: "inflight", x: 540, y: 150, label: "IN FLIGHT" },
  { id: "executor", x: 780, y: 150, label: "EXECUTOR" },
  { id: "gate", x: 1020, y: 150, label: "REVIEW GATE" },
  { id: "human", x: 1020, y: 370, label: "HUMAN" },
];
const NW = 180, NH = 96;
const EDGES = [
  ["advisor", "queue", "seq"], ["queue", "inflight", "seq"], ["inflight", "executor", "seq"],
  ["executor", "gate", "seq"], ["gate", "human", "branch"], ["human", "advisor", "loop"], ["gate", "advisor", "loop"],
];
let cam = null; // {x, y, k}

function nodeCenter(id) { const n = NODES.find((n) => n.id === id); return { x: n.x + NW / 2, y: n.y + NH / 2 }; }

function renderCanvas(panel, s) {
  const first = !panel.querySelector(".loop-canvas");
  if (first) buildCanvasShell(panel);
  patchCanvas(s);
}

function buildCanvasShell(panel) {
  panel.replaceChildren();
  const wrap = el("div", "canvas-wrap");
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("class", "loop-canvas");
  svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
  const root = document.createElementNS(NS, "g");
  root.setAttribute("id", "cam");
  const edgeLayer = document.createElementNS(NS, "g"); edgeLayer.setAttribute("id", "edges");
  const nodeLayer = document.createElementNS(NS, "g"); nodeLayer.setAttribute("id", "nodes");
  root.append(edgeLayer, nodeLayer);
  svg.append(root);
  wrap.append(svg);

  const legend = el("div", "canvas-legend");
  [["var(--go)", "active"], ["var(--flow)", "in flight"], ["var(--human)", "needs you"], ["var(--wait)", "idle"]].forEach(([c, t]) => {
    const span = el("span"); const i = el("i"); i.style.background = c; span.append(i, document.createTextNode(t)); legend.append(span);
  });
  wrap.append(legend);
  wrap.append((() => { const h = el("div", "canvas-hint", "drag to pan · scroll to zoom"); return h; })());
  const reset = el("button", "btn ghost sm canvas-reset", "Fit");
  reset.onclick = () => { fitCanvas(svg); };
  wrap.append(reset);
  panel.append(wrap);

  buildNodes(nodeLayer);
  buildEdges(edgeLayer);
  wireCanvasPanZoom(svg, root);
  requestAnimationFrame(() => fitCanvas(svg));
}

function buildNodes(layer) {
  const NS = "http://www.w3.org/2000/svg";
  for (const n of NODES) {
    const g = document.createElementNS(NS, "g");
    g.setAttribute("class", "node-card");
    g.setAttribute("data-node", n.id);
    g.setAttribute("transform", `translate(${n.x},${n.y})`);
    const box = document.createElementNS(NS, "rect");
    box.setAttribute("class", "node-box"); box.setAttribute("rx", "10");
    box.setAttribute("width", NW); box.setAttribute("height", NH); box.setAttribute("data-box", "");
    const title = document.createElementNS(NS, "text");
    title.setAttribute("class", "node-title"); title.setAttribute("x", 16); title.setAttribute("y", 26); title.textContent = n.label;
    const count = document.createElementNS(NS, "text");
    count.setAttribute("class", "node-count"); count.setAttribute("x", 16); count.setAttribute("y", 62); count.setAttribute("data-count", "");
    const sub = document.createElementNS(NS, "text");
    sub.setAttribute("class", "node-sub"); sub.setAttribute("x", 16); sub.setAttribute("y", 82); sub.setAttribute("data-sub", "");
    const pip = document.createElementNS(NS, "circle");
    pip.setAttribute("cx", NW - 18); pip.setAttribute("cy", 20); pip.setAttribute("r", 5); pip.setAttribute("data-pip", "");
    g.append(box, title, count, sub, pip);
    if (n.id === "human") { const halo = document.createElementNS(NS, "rect"); halo.setAttribute("rx", "12"); halo.setAttribute("x", -4); halo.setAttribute("y", -4); halo.setAttribute("width", NW + 8); halo.setAttribute("height", NH + 8); halo.setAttribute("fill", "none"); halo.setAttribute("data-halo", ""); g.insertBefore(halo, box); }
    g.onclick = () => onNodeClick(n.id);
    layer.append(g);
  }
}

function buildEdges(layer) {
  const NS = "http://www.w3.org/2000/svg";
  for (const [from, to, kind] of EDGES) {
    const a = nodeCenter(from), b = nodeCenter(to);
    const path = document.createElementNS(NS, "path");
    path.setAttribute("class", "edge"); path.setAttribute("data-edge", `${from}-${to}`);
    path.setAttribute("d", edgePath(a, b, kind));
    const flow = document.createElementNS(NS, "path");
    flow.setAttribute("class", "edge-dash"); flow.setAttribute("data-flow", `${from}-${to}`);
    flow.setAttribute("d", edgePath(a, b, kind)); flow.style.display = "none";
    layer.append(path, flow);
  }
}

function edgePath(a, b, kind) {
  if (kind === "loop") {
    const midY = Math.max(a.y, b.y) + 180;
    return `M ${a.x} ${a.y + NH / 2 - 10} C ${a.x} ${midY}, ${b.x} ${midY}, ${b.x} ${b.y + NH / 2 - 10}`;
  }
  if (kind === "branch") return `M ${a.x} ${a.y} C ${a.x} ${(a.y + b.y) / 2}, ${b.x} ${(a.y + b.y) / 2}, ${b.x} ${b.y - NH / 2}`;
  const mx = (a.x + b.x) / 2;
  return `M ${a.x + NW / 2} ${a.y} C ${mx} ${a.y}, ${mx} ${b.y}, ${b.x - NW / 2} ${b.y}`;
}

function patchCanvas(s) {
  const counts = deriveCanvas(s);
  for (const n of NODES) {
    const g = document.querySelector(`[data-node="${n.id}"]`); if (!g) continue;
    const d = counts[n.id];
    g.querySelector("[data-count]").textContent = d.big;
    g.querySelector("[data-sub]").textContent = d.sub;
    const box = g.querySelector("[data-box]");
    box.classList.toggle("active", d.mood === "active");
    box.classList.toggle("blocked", d.mood === "blocked");
    box.classList.toggle("warn", d.mood === "warn");
    const pip = g.querySelector("[data-pip]");
    pip.setAttribute("fill", d.pip);
    const halo = g.querySelector("[data-halo]");
    if (halo) {
      halo.setAttribute("stroke", d.mood === "blocked" ? "var(--human)" : "transparent");
      halo.setAttribute("stroke-width", "2");
      halo.style.animation = d.mood === "blocked" ? "live-pulse 2.2s ease-out infinite" : "none";
    }
  }
  // flow only where work is actually moving
  const active = new Set();
  if (counts.queue.n > 0) active.add("advisor-queue");
  if (counts.inflight.n > 0) { active.add("queue-inflight"); active.add("inflight-executor"); }
  if (counts.executor.n > 0) active.add("executor-gate");
  document.querySelectorAll("[data-flow]").forEach((f) => { f.style.display = active.has(f.dataset.flow) ? "" : "none"; });
}

function deriveCanvas(s) {
  const by = (pred) => s.queue.filter(pred).length;
  const decisions = s.decisions.length;
  const sessions = s.sessions.filter((x) => !x.stale);
  const execSess = sessions.filter((x) => x.role === "executor" || x.role === "project-loop");
  const queued = by((j) => j.status === "queued");
  const inflight = by((j) => INFLIGHT.has(j.status));
  return {
    advisor: { big: s.server.advisor.split("-")[0], sub: "plans · reviews", mood: inflight ? "active" : "idle", pip: inflight ? "var(--flow)" : "var(--wait)" },
    queue: { big: String(queued), sub: queued === 1 ? "job queued" : "jobs queued", n: queued, mood: queued ? "active" : "idle", pip: queued ? "var(--flow)" : "var(--wait)" },
    inflight: { big: String(inflight), sub: "awaiting advisor", n: inflight, mood: inflight ? "warn" : "idle", pip: inflight ? "var(--lock)" : "var(--wait)" },
    executor: { big: String(execSess.length), sub: execSess[0] ? execSess[0].projectId || "working" : "idle", n: execSess.length, mood: execSess.length ? "active" : "idle", pip: execSess.length ? "var(--go)" : "var(--wait)" },
    gate: { big: String(by((j) => j.status === "needs_recovery")), sub: "review · recover", mood: by((j) => j.status === "needs_recovery") ? "warn" : "idle", pip: by((j) => j.status === "needs_recovery") ? "var(--quota)" : "var(--wait)" },
    human: { big: String(decisions), sub: decisions === 1 ? "decision open" : "decisions open", mood: decisions ? "blocked" : "idle", pip: decisions ? "var(--human)" : "var(--wait)" },
  };
}

function onNodeClick(id) {
  if (id === "human") { setView("canvas"); bind("rail-body").scrollIntoView({ behavior: "smooth" }); flashRail(); return; }
  if (id === "queue" || id === "inflight" || id === "gate") setView("queue");
  if (id === "executor") setView("board");
  if (id === "advisor") setView("artifacts");
}
function flashRail() { const r = $(".rail"); r.animate([{ background: "var(--elevated)" }, { background: "var(--surface)" }], { duration: 900 }); }

function wireCanvasPanZoom(svg, root) {
  // capture the pointer only once real movement starts — capturing on
  // pointerdown retargets the click to the svg and kills node navigation
  let down = false, dragging = false, sx = 0, sy = 0;
  svg.addEventListener("pointerdown", (e) => { down = true; dragging = false; sx = e.clientX; sy = e.clientY; });
  svg.addEventListener("pointermove", (e) => {
    if (!down || !cam) return;
    if (!dragging) {
      if (Math.abs(e.clientX - sx) + Math.abs(e.clientY - sy) < 5) return;
      dragging = true; svg.classList.add("panning");
      try { svg.setPointerCapture(e.pointerId); } catch (_) {}
    }
    cam.x += e.clientX - sx; cam.y += e.clientY - sy; sx = e.clientX; sy = e.clientY; applyCam(root);
  });
  const stopDrag = (e) => { down = false; dragging = false; svg.classList.remove("panning"); try { svg.releasePointerCapture(e.pointerId); } catch (_) {} };
  svg.addEventListener("pointerup", stopDrag);
  svg.addEventListener("pointercancel", stopDrag);
  svg.addEventListener("wheel", (e) => {
    e.preventDefault(); if (!cam) return;
    const rect = svg.getBoundingClientRect();
    const px = e.clientX - rect.left, py = e.clientY - rect.top;
    const factor = Math.exp(-e.deltaY * 0.0015);
    const k2 = Math.max(0.4, Math.min(2.4, cam.k * factor));
    cam.x = px - (px - cam.x) * (k2 / cam.k);
    cam.y = py - (py - cam.y) * (k2 / cam.k);
    cam.k = k2; applyCam(root);
  }, { passive: false });
  svg.addEventListener("dblclick", () => fitCanvas(svg));
}
function applyCam(root) { root.setAttribute("transform", `translate(${cam.x},${cam.y}) scale(${cam.k})`); }
function fitCanvas(svg) {
  const rect = svg.getBoundingClientRect();
  const minX = Math.min(...NODES.map((n) => n.x)) - 40;
  const minY = Math.min(...NODES.map((n) => n.y)) - 40;
  const maxX = Math.max(...NODES.map((n) => n.x + NW)) + 40;
  const maxY = Math.max(...NODES.map((n) => n.y + NH)) + 220;
  const w = maxX - minX, h = maxY - minY;
  const k = Math.min(rect.width / w, rect.height / h, 1.6);
  cam = { k, x: (rect.width - w * k) / 2 - minX * k, y: (rect.height - h * k) / 2 - minY * k };
  applyCam($("#cam", svg));
}

/* ---- board view (kanban by status) ------------------------------------ */

const COLUMNS = [
  ["queued", "Queued", "var(--flow)"],
  ["claimed", "Claimed", "var(--lock)"],
  ["submitted", "Submitted", "var(--lock)"],
  ["waiting_for_chatgpt", "Waiting", "var(--flow)"],
  ["needs_recovery", "Needs recovery", "var(--quota)"],
  ["done", "Done", "var(--wait)"],
];
function renderBoard(panel, s) {
  panel.replaceChildren(viewHead("Board", `${s.queue.length} active · ${s.hiddenTerminalJobs} finished`));
  const board = el("div", "board");
  const now = Date.now();
  for (const [key, label, color] of COLUMNS) {
    const jobs = key === "done"
      ? [] // terminal jobs are hidden from status; the board shows the live pipeline
      : s.queue.filter((j) => j.status === key);
    const col = el("div", "board-col");
    const head = el("div", "board-col-head");
    const dot = el("i"); dot.style.background = color;
    head.append(dot, el("span", null, label), el("span", "n", String(jobs.length)));
    col.append(head);
    const bodyc = el("div", "board-col-body");
    if (key === "done") { const n = el("div", "rail-empty", `${s.hiddenTerminalJobs} finished (hidden from live pipeline)`); n.style.padding = "8px 4px"; bodyc.append(n); }
    for (const j of jobs) bodyc.append(jobCard(j, now));
    col.append(bodyc);
    board.append(col);
  }
  panel.append(board);
}
function jobCard(j, now) {
  const card = el("div", "jobcard");
  card.append(el("div", "j-id", j.jobId));
  const meta = el("div", "j-meta"); meta.append(el("span", null, j.projectId), el("span", null, `attempt ${j.attemptCount}`));
  card.append(meta);
  const foot = el("div", "j-foot");
  const eta = j.status === "queued" ? "ready" : rel(j.nextPollAt, now);
  const etaEl = el("span", `j-eta${eta.endsWith("ago") ? " overdue" : ""}`, eta.endsWith("ago") ? `overdue ${eta.replace(" ago", "")}` : eta);
  foot.append(etaEl);
  card.append(foot);
  return card;
}

/* ---- queue table ------------------------------------------------------- */

function renderQueue(panel, s) {
  panel.replaceChildren(viewHead("Queue", `${s.queue.length} active jobs`));
  if (!s.queue.length) { panel.append(emptyState("Queue is empty.", "Create a job to start a loop.", "+ Job", () => openJobModal())); return; }
  const now = Date.now();
  const table = el("table", "tbl");
  const thead = el("thead"); const htr = el("tr");
  ["Job", "Project", "Status", "Att", "Next poll", "Last error", ""].forEach((h) => { const th = el("th", null, h); htr.append(th); });
  thead.append(htr); table.append(thead);
  const tb = el("tbody");
  for (const j of s.queue) {
    const tr = el("tr");
    tr.append(td("mono-id", j.jobId), td(null, j.projectId));
    const st = el("td"); const pill = el("span", null, j.status); pill.style.cssText = `color:${JOB_STATE[j.status] || "var(--text)"}`; st.append(pill); tr.append(st);
    tr.append(td("num", String(j.attemptCount)));
    const eta = j.status === "queued" ? "ready" : rel(j.nextPollAt, now);
    tr.append(td(eta.endsWith("ago") ? "num overdue" : "num", eta.endsWith("ago") ? `overdue ${eta.replace(" ago", "")}` : eta));
    const err = td(null, j.lastError || "—"); err.style.color = "var(--text-faint)"; err.style.maxWidth = "220px"; err.style.overflow = "hidden"; err.style.textOverflow = "ellipsis"; err.style.whiteSpace = "nowrap"; err.title = j.lastError || ""; tr.append(err);
    const act = el("td");
    if (!TERMINAL.has(j.status)) {
      const r = el("button", "btn ghost sm", "Recover"); r.onclick = () => runRecover(j.jobId, true, r); act.append(r);
    }
    tr.append(act);
    tb.append(tr);
  }
  table.append(tb);
  panel.append(table);
}
function td(cls, text) { return el("td", cls, text); }

/* ---- artifacts view ---------------------------------------------------- */

async function renderArtifacts(panel, s) {
  const project = store.project || (s.projects[0] && s.projects[0].projectId);
  panel.replaceChildren(viewHead("Artifacts", project ? `project ${project}` : "no project"));
  if (!project) { panel.append(emptyState("No projects yet.", "Artifacts appear once the advisor produces one.")); return; }
  const grid = el("div", "artifacts");
  const listCol = el("div", "artifact-list");
  const doc = el("div", "artifact-doc"); doc.append(el("div", "rail-empty", "Select an artifact to read it."));
  grid.append(listCol, doc);
  panel.append(grid);
  try {
    const items = await getJSON(`/api/artifacts?project=${encodeURIComponent(project)}`);
    if (!items.length) { listCol.append(el("div", "rail-empty", "No captured artifacts yet.")); return; }
    for (const a of items) {
      const item = el("div", "artifact-item");
      item.append(el("div", "a-kind", a.kind || "artifact"));
      const meta = el("div", "a-meta"); meta.append(el("span", null, (a.created_at || "").replace("T", " ").replace("Z", "")));
      if (a.control_block_valid === false || a.control_block_valid === "false") {
        const bad = el("span", null, "control block invalid"); bad.style.cssText = "color:var(--quota);border:1px solid color-mix(in srgb,var(--quota) 40%,transparent);border-radius:4px;padding:0 5px"; meta.append(bad);
      } else if (a.approval_decision) { const ok = el("span", null, a.approval_decision); ok.style.color = "var(--go)"; meta.append(ok); }
      item.append(meta);
      item.onclick = () => { listCol.querySelectorAll(".artifact-item").forEach((x) => x.classList.remove("is-active")); item.classList.add("is-active"); loadArtifact(a.path, doc); };
      listCol.append(item);
    }
  } catch (e) { listCol.append(el("div", "rail-empty", `Could not list artifacts: ${e.message}`)); }
}
async function loadArtifact(path, doc) {
  doc.replaceChildren(el("div", "rail-empty", "Loading…"));
  try { const text = await getText(`/api/file?path=${encodeURIComponent(path)}`); const pre = el("pre"); pre.textContent = text; doc.replaceChildren(pre); }
  catch (e) { doc.replaceChildren(el("div", "rail-empty", `Could not read: ${e.message}`)); }
}

/* ---- events view ------------------------------------------------------- */

function renderEvents(panel, s) {
  panel.replaceChildren(viewHead("Activity", "queue events, newest first"));
  const wrap = el("div", "events");
  if (!store.events.length) { wrap.append(el("div", "rail-empty", store.eventsLoaded ? "No events yet." : "Loading events…")); }
  let dividerPlaced = !store.prevVisit;
  for (const ev of store.events) {
    const at = parseAt(ev.at);
    if (!dividerPlaced && at && at.getTime() <= store.prevVisit) {
      if (wrap.children.length) wrap.append(el("div", "ev-divider", "seen before your last visit"));
      dividerPlaced = true;
    }
    const row = el("div", "evrow");
    row.append(el("div", "ev-at", (ev.at || "").replace("T", " ").replace("Z", "")));
    const type = el("div", "ev-type"); const b = el("b", null, ev.event_type); type.append(b, document.createTextNode(" " + (ev.job_id || ""))); row.append(type);
    const data = ev.data && Object.keys(ev.data).length ? JSON.stringify(ev.data) : "";
    row.append(el("div", "ev-data", data));
    wrap.append(row);
  }
  panel.append(wrap);
}

/* ---- shared view chrome ------------------------------------------------ */

function viewHead(title, sub) {
  const head = el("div", "view-head");
  head.append(el("h2", null, title));
  if (sub) head.append(el("span", "sub", sub));
  return head;
}
function emptyState(title, body, cta, onCta) {
  const box = el("div", "rail-empty"); box.style.cssText = "padding:40px var(--pad);max-width:420px";
  box.append(el("div", null, title));
  const p = el("div", null, body); p.style.color = "var(--text-faint)"; p.style.marginTop = "6px"; box.append(p);
  if (cta) { const b = el("button", "btn primary sm", cta); b.style.marginTop = "12px"; b.onclick = onCta; box.append(b); }
  return box;
}

/* ---- create-job modal -------------------------------------------------- */

function openJobModal() {
  const s = store.status;
  const body = bind("modal-body");
  body.replaceChildren();
  bind("modal-title").textContent = "Create queue job";
  const fields = {};
  const add = (key, label, hint, value = "") => {
    const f = el("div", "field");
    const lab = el("label", null, label); lab.htmlFor = `f-${key}`; f.append(lab);
    const input = el("input"); input.id = `f-${key}`; input.value = value; input.placeholder = hint || ""; fields[key] = input; f.append(input);
    body.append(f);
  };
  const proj = (s && s.projects[0] && s.projects[0].projectId) || "MY_APP";
  add("job_id", "Job id", "BQ-" + proj + "-001", "BQ-" + proj + "-001");
  add("project_id", "Project", proj, proj);
  add("target_chat_title", "Advisor conversation title", proj + " planning");
  add("target_chat_url", "Advisor target URL", "manual");
  add("expected_marker", "Expected marker (optional)", "");
  const actions = el("div", "modal-actions");
  const cancel = el("button", "btn ghost", "Cancel"); cancel.onclick = closeModal;
  const create = el("button", "btn primary", "Create job");
  create.onclick = async () => {
    const payload = {}; for (const k in fields) payload[k] = fields[k].value.trim();
    create.disabled = true;
    try { await action("/api/actions/create-job", payload); toast("ok", "Job created", payload.job_id); closeModal(); poll(); }
    catch (e) { toast("err", "Could not create job", e.message); create.disabled = false; }
  };
  actions.append(cancel, create); body.append(actions);
  showModal(fields.job_id);
}

let modalOpener = null;
function showModal(focusTarget) {
  modalOpener = document.activeElement;
  bind("modal").hidden = false;
  const target = focusTarget || $(".modal button, .modal input, .modal textarea");
  if (target) target.focus();
}
function closeModal() {
  bind("modal").hidden = true;
  if (modalOpener && typeof modalOpener.focus === "function") modalOpener.focus();
  modalOpener = null;
}

/* ---- files + toasts ---------------------------------------------------- */

async function openFile(path, title) {
  const body = bind("modal-body"); body.replaceChildren();
  bind("modal-title").textContent = title || path;
  const pre = el("pre"); pre.style.cssText = "white-space:pre-wrap;max-height:60vh;overflow:auto;font-size:12px;line-height:1.6;margin:0";
  pre.textContent = "Loading…"; body.append(pre);
  showModal(document.querySelector('[data-action="modal-close"]'));
  try { pre.textContent = await getText(`/api/file?path=${encodeURIComponent(path)}`); }
  catch (e) { pre.textContent = "Could not read file: " + e.message; }
}

function toast(kind, title, body) {
  const stack = bind("toasts");
  const t = el("div", `toast ${kind}`);
  t.append(el("div", "t-title", title));
  if (body) t.append(el("div", "t-body", body));
  stack.append(t);
  setTimeout(() => { t.style.transition = "opacity .3s"; t.style.opacity = "0"; setTimeout(() => t.remove(), 320); }, 3600);
}

/* ---- boot -------------------------------------------------------------- */

const VIEW_KEYS = { 1: "canvas", 2: "board", 3: "queue", 4: "artifacts", 5: "events" };
document.querySelectorAll(".railnav-item").forEach((b, i) => {
  b.onclick = () => setView(b.dataset.view);
  if (i < 5) b.title = `Shortcut: ${i + 1}`;
});
document.querySelector('[data-action="new-job"]').onclick = openJobModal;
document.querySelector('[data-action="modal-close"]').onclick = closeModal;
bind("modal").addEventListener("click", (e) => { if (e.target === bind("modal")) closeModal(); });
bind("modal").addEventListener("keydown", (e) => {
  if (e.key !== "Tab") return;
  const focusables = bind("modal").querySelectorAll("button, input, textarea, select, a[href]");
  if (!focusables.length) return;
  const first = focusables[0], last = focusables[focusables.length - 1];
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") return closeModal();
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const tag = document.activeElement && document.activeElement.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
  if (VIEW_KEYS[e.key]) setView(VIEW_KEYS[e.key]);
});

let timer = null;
function startPolling() { poll(); timer = setInterval(() => { if (!document.hidden) poll(); }, POLL_MS); }
document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
startPolling();
