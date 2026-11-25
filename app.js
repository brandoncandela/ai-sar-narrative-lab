// SAR Narrative Lab: renders committed drafts, their evaluation and the review log from data/lab.json.
// No model calls and no outside requests: the only fetch is the local data file.

const MODEL_NAMES = { "claude-opus-5-5": "Claude Opus 5.5", "claude-opus-4-8": "Claude Opus 4.8" };
const STORE_KEY = "sarlab.reviews.v1";
const NAME_KEY = "sarlab.reviewer";
const S = { lab: null, byId: {}, filter: "all", caseId: null, focus: null, decision: null, local: {} };

const $ = (sel) => document.querySelector(sel);
function el(tag, attrs = {}, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false) node.append(kid);
  return node;
}
const money = (n) => "$" + Number(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const num = (n) => Number(n).toLocaleString("en-US");
function day(iso) {
  if (!iso) return "";
  const d = new Date(iso.length === 10 ? iso + "T12:00:00Z" : iso);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
}
const modelName = (id) => MODEL_NAMES[id] || id;
const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1).replace(/_/g, " ") : "");
const verdictOf = (c) => (c.evaluation ? c.evaluation.verdict : "none");
const VERDICT_TEXT = { pass: "Pass", warn: "Warnings", fail: "Fail", none: "Not drafted" };
const pill = (v) => el("span", { class: `pill ${v}`, text: VERDICT_TEXT[v] });

/* ---------- motion: only ever plays on content that is already rendered, and never under reduced motion ---------- */
const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
const CAN_ANIMATE = !REDUCED && "IntersectionObserver" in window;
function whenVisible(node, fn, threshold = 0.25) {
  if (!CAN_ANIMATE || !node) return;
  const io = new IntersectionObserver((entries) => { if (entries.some((e) => e.isIntersecting)) { io.disconnect(); fn(); } }, { threshold });
  io.observe(node);
}
function countUp(node, value, render = num) {
  node.textContent = render(CAN_ANIMATE ? 0 : value);
  if (!CAN_ANIMATE || !value) { node.textContent = render(value); return; }
  whenVisible(node, () => {
    const start = performance.now();
    const step = (t) => {
      const p = Math.min(1, (t - start) / 900);
      node.textContent = render(Math.round(value * (1 - Math.pow(1 - p, 3))));
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  });
}
function peek(id, on) {
  document.querySelectorAll(`#evidence-body [data-ev="${CSS.escape(id)}"]`).forEach((n) => n.classList.toggle("peek", on));
  const c = current();
  const n = c && c.draft && c.draft.narrative;
  if (!n) return;
  S.lab.sections.forEach(({ key }) => (n[key] || []).forEach((s, i) => {
    if (s.citations.includes(id)) document.querySelector(`.sentence[data-key="${key}:${i}"]`)?.classList.toggle("peek", on);
  }));
}

// The draft types itself in the first time the workspace scrolls into view. A click, a case change or a
// safety timer shows the whole text at once, so nothing can stay hidden.
let typedOnce = false;
function typeNarrative() {
  const body = $("#narrative-body");
  const nodes = [...body.querySelectorAll(".sentence .text")];
  if (!nodes.length || S.decision === "edit") return;
  typedOnce = true;
  const texts = nodes.map((n) => n.textContent);
  const total = texts.reduce((a, t) => a + t.length, 0);
  const DURATION = 2400; // ms for the whole draft, whatever the frame rate
  let i = 0, pos = 0, shown = 0, frame = 0, done = false;
  const t0 = performance.now();
  const sentence = (k) => nodes[k].closest(".sentence");
  const finish = () => {
    if (done) return;
    done = true;
    cancelAnimationFrame(frame);
    clearTimeout(safety);
    nodes.forEach((n, k) => { n.textContent = texts[k]; sentence(k).classList.remove("pending", "active"); });
    body.removeAttribute("aria-busy");
    body.removeEventListener("click", finish, true);
    S.cancelTyping = null;
  };
  nodes.forEach((n, k) => { n.textContent = ""; sentence(k).classList.add("pending"); });
  body.setAttribute("aria-busy", "true");
  body.addEventListener("click", finish, true);
  const safety = setTimeout(finish, DURATION + 1600);
  const step = () => {
    let budget = Math.min(total, Math.ceil((total * (performance.now() - t0)) / DURATION)) - shown;
    shown += Math.max(0, budget);
    while (budget > 0 && i < nodes.length) {
      sentence(i).classList.remove("pending");
      sentence(i).classList.add("active");
      const take = Math.min(budget, texts[i].length - pos);
      pos += take;
      budget -= take;
      nodes[i].textContent = texts[i].slice(0, pos);
      if (pos >= texts[i].length) { sentence(i).classList.remove("active"); i += 1; pos = 0; }
    }
    if (i < nodes.length) frame = requestAnimationFrame(step); else finish();
  };
  S.cancelTyping = finish;
  frame = requestAnimationFrame(step);
}

function loadLocal() {
  try { S.local = JSON.parse(localStorage.getItem(STORE_KEY) || "{}") || {}; } catch { S.local = {}; }
  try { $("#reviewer-name").value = localStorage.getItem(NAME_KEY) || ""; } catch { /* storage blocked */ }
}
function saveLocal() {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(S.local)); return true; } catch { return false; }
}

/* ---------- hero ---------- */
function renderHero() {
  const { meta, evaluation: ev, faults } = S.lab;
  $("#model-label").textContent = meta.drafts ? modelName(meta.model) : "Not generated yet";
  const passed = ev.drafts ? ev.pass + ev.warn : 0;
  const stats = [
    ["Drafts", meta.drafts, (v) => `${v} of ${meta.cases}`],
    ["Pass hard checks", passed, (v) => `${v} of ${ev.drafts}`],
    ["Values checked", ev.values_checked, num],
    ["Planted errors caught", faults.caught, (v) => `${v} of ${faults.planted}`],
  ];
  $("#hero-stats").replaceChildren(...stats.map(([k, v, render]) => {
    const dd = el("dd");
    if (meta.drafts) countUp(dd, v, render); else dd.textContent = "None yet";
    return el("div", {}, el("dt", { text: k }), dd);
  }));
  if (meta.drafts) {
    $("#flow-model").textContent = `${modelName(meta.model)} at ${meta.effort} effort. Structured output forces who, what, when, where and why, with a citations list on every sentence.`;
    const cost = meta.usage.estimated_cost_usd ? ` Estimated API cost for all drafts: $${meta.usage.estimated_cost_usd.toFixed(2)}.` : "";
    $("#footer-note").textContent = `Fictional bank, customers and alerts. Drafts generated with ${modelName(meta.model)} on ${day(meta.first_generated)}.${cost} Not a real SAR.`;
  }
  $("#system-prompt").textContent = S.lab.system_prompt;
}

/* ---------- queue ---------- */
function visibleCases() {
  return S.lab.cases.filter((c) => S.filter === "all" || verdictOf(c) === S.filter);
}
function renderQueue() {
  const cases = visibleCases();
  $("#queue-count").textContent = `${cases.length} of ${S.lab.cases.length}`;
  $("#queue-list").replaceChildren(...cases.map((c) => el("li", {},
    el("button", { type: "button", "aria-current": c.case_id === S.caseId ? "true" : "false", onclick: () => selectCase(c.case_id) },
      el("span", { class: "q-id", text: c.case_id }), pill(verdictOf(c)),
      el("span", { class: "q-sub" }, `${c.subject.name} · ${cap(c.primary_typology)}`,
        S.local[c.case_id] ? el("span", { class: "q-local", text: ` · ${S.local[c.case_id].decision}ed` }) : null)))));
  const select = $("#case-select");
  select.replaceChildren(...cases.map((c) => el("option", { value: c.case_id, selected: c.case_id === S.caseId,
    text: `${c.case_id} · ${VERDICT_TEXT[verdictOf(c)]} · ${cap(c.primary_typology)}` })));
}

function selectCase(id) {
  S.caseId = id;
  S.focus = null;
  const local = S.local[id];
  S.decision = local ? local.decision : null;
  renderQueue();
  renderCase();
}

/* ---------- one case ---------- */
function current() { return S.byId[S.caseId]; }

function sentenceIssues(caseData, key, i) {
  const ev = caseData.evaluation;
  return ev && ev.sections[key] && ev.sections[key][i] ? ev.sections[key][i].issues : [];
}
function severity(check) { return S.lab.evaluation.by_check[check]?.severity || "fail"; }

function renderNarrative() {
  if (S.cancelTyping) S.cancelTyping();
  const c = current();
  const body = $("#narrative-body");
  const editing = S.decision === "edit";
  if (!c.draft || !c.draft.narrative) {
    const why = c.draft ? `The model returned no usable draft: ${c.draft.error}.` : "This case has not been drafted yet. Run src/generate.py locally to create it.";
    body.replaceChildren(el("p", { class: "empty", text: why }));
    $("#open-questions").replaceChildren();
    return;
  }
  const n = editingNarrative();
  const ids = new Set(Object.keys(evidenceIndex(c)));
  body.replaceChildren(...S.lab.sections.map(({ key, title }) => {
    const rows = n[key] || [];
    const section = el("section", { class: "nar-section", "aria-label": title }, el("h4", { text: title }));
    if (!rows.length) section.append(el("p", { class: "section-issue", text: "This section is empty." }));
    rows.forEach((s, i) => {
      if (editing) {
        section.append(el("div", { class: "edit-sentence" },
          el("label", { class: "sr-only", for: `ed-${key}-${i}`, text: `${title} sentence ${i + 1}` }),
          el("textarea", { id: `ed-${key}-${i}`, rows: 2, "data-key": key, "data-i": i, "data-field": "text", oninput: onEditInput }, s.text),
          el("input", { type: "text", value: s.citations.join(", "), "aria-label": `Citations for ${title} sentence ${i + 1}`, "data-key": key, "data-i": i, "data-field": "citations", oninput: onEditInput })));
        return;
      }
      const issues = sentenceIssues(c, key, i);
      const hard = issues.some((x) => severity(x.check) === "fail");
      const cites = s.citations.length
        ? s.citations.map((id) => el("button", { type: "button", class: `cite${ids.has(id) ? "" : " bad"}`, "data-ev": id, title: ids.has(id) ? "Show this evidence" : "Not in the case packet",
            onclick: (e) => { e.stopPropagation(); focusEvidence(id); },
            onmouseenter: () => peek(id, true), onmouseleave: () => peek(id, false), onfocus: () => peek(id, true), onblur: () => peek(id, false) }, id))
        : [el("span", { class: "cite none", text: "no citation" })];
      section.append(el("div", { class: `sentence${hard ? " flagged" : issues.length ? " warned" : ""}`, "data-key": `${key}:${i}`, tabindex: 0,
          onclick: () => focusSentence(`${key}:${i}`), onkeydown: (e) => { if (e.key === "Enter") focusSentence(`${key}:${i}`); } },
        el("span", { class: "text", text: s.text }), el("span", { class: "cites" }, cites),
        issues.length ? el("ul", { class: "issue-list" }, issues.map((x) => el("li", { class: severity(x.check) === "warn" ? "warn" : "", text: x.detail }))) : null));
    });
    return section;
  }));
  const qs = n.open_questions || [];
  $("#open-questions").replaceChildren(...(qs.length && !editing ? [el("h4", { text: "Open questions the draft leaves for the investigator" }), el("ul", {}, qs.map((q) => el("li", { text: q })))] : []));
  applyFocus();
}

function evidenceIndex(c) {
  const idx = { [c.subject.evidence_id]: c.subject, [c.summary.evidence_id]: c.summary };
  c.alerts.forEach((a) => { idx[a.evidence_id] = a; });
  return idx;
}

function renderEvidence() {
  const c = current();
  const cited = new Set();
  const n = c.draft && c.draft.narrative;
  if (n) S.lab.sections.forEach(({ key }) => (n[key] || []).forEach((s) => s.citations.forEach((id) => cited.add(id))));
  const s = c.subject;
  $("#evidence-count").textContent = `${c.alerts.length + 2} records`;
  const kyc = el("div", { class: "ev-block", "data-ev": s.evidence_id },
    el("span", { class: "ev-id", text: s.evidence_id }),
    el("dl", { class: "kyc" },
      ...[["Name", s.name], ["Born", day(s.date_of_birth)], ["ZIP", s.zip_code], ["Segment", cap(s.segment)], ["Risk rating", s.risk_rating], ["Accounts", s.accounts.map((a) => a.replace("account ", "")).join(", ")]]
        .flatMap(([k, v]) => [el("dt", { text: k }), el("dd", { text: v || "Not recorded" })])));
  kyc.addEventListener("click", () => focusEvidence(s.evidence_id));
  const alerts = el("ol", { class: "alert-list", "aria-label": "Alerts" }, c.alerts.map((a) => el("li", {},
    el("button", { type: "button", class: cited.has(a.evidence_id) || !n ? "" : "uncited", "data-ev": a.evidence_id,
        title: cited.has(a.evidence_id) || !n ? a.amount_meaning : "The narrative never cites this alert", onclick: () => focusEvidence(a.evidence_id),
        onmouseenter: () => peek(a.evidence_id, true), onmouseleave: () => peek(a.evidence_id, false) },
      el("span", { class: "ev-id", text: a.evidence_id }), el("span", { class: "a-amt", text: money(a.amount) }),
      el("span", { class: "a-sub", text: [day(a.date), a.scenario_label, a.referral_reason, a.branch ? `branch ${a.branch}` : null].filter(Boolean).join(" · ") })))));
  const sum = c.summary;
  const totals = el("div", { class: "ev-block case-totals", "data-ev": sum.evidence_id },
    el("span", { class: "ev-id", text: sum.evidence_id }),
    el("div", { text: `${sum.alert_count} alerts from ${day(sum.first_alert_date)} to ${day(sum.last_alert_date)}, ${money(sum.total_amount)} in total across ${sum.systems.length} source${sum.systems.length > 1 ? "s" : ""}.` }));
  totals.addEventListener("click", () => focusEvidence(sum.evidence_id));
  $("#evidence-body").replaceChildren(kyc, alerts, totals);
}

function renderChecks() {
  const c = current();
  const list = $("#check-list");
  if (!c.evaluation) { list.replaceChildren(el("li", { class: "n", text: "No draft to check." })); return; }
  list.replaceChildren(...Object.entries(c.evaluation.checks).map(([, chk]) => {
    const bad = chk.issues > 0;
    const cls = !bad ? "ok" : chk.severity === "warn" ? "w" : "bad";
    return el("li", {}, el("span", { class: `mark ${cls}`, "aria-hidden": "true", text: !bad ? "✓" : chk.severity === "warn" ? "!" : "✕" }),
      el("span", { text: chk.label }), el("span", { class: "n", text: bad ? `${chk.issues} issue${chk.issues > 1 ? "s" : ""}` : "clean" }));
  }));
  const extra = c.evaluation.draft_issues.filter((x) => x.check === "alert_coverage").map((x) => x.detail);
  if (extra.length) list.append(el("li", { class: "n" }, el("span"), el("span", { text: extra.join("; ") })));
}

function renderCase() {
  const c = current();
  if (!c) return;
  $("#case-kicker").textContent = `${c.case_id} · ${cap(c.primary_typology)}`;
  $("#case-verdict").replaceChildren(pill(verdictOf(c)));
  $("#draft-meta").textContent = c.draft
    ? `Drafted by ${modelName(c.draft.model_served)}${c.draft.fallback_used ? " (fallback)" : ""} on ${day(c.draft.generated_at)} · ${num(c.draft.usage.output_tokens)} output tokens`
    : "Not drafted yet";
  renderNarrative();
  renderEvidence();
  renderChecks();
  renderReviewPanel();
}

/* ---------- focus links between sentences and evidence ---------- */
function focusSentence(key) { S.focus = S.focus?.sentence === key ? null : { sentence: key }; applyFocus(); }
function focusEvidence(id) {
  S.focus = S.focus?.evidence === id ? null : { evidence: id };
  applyFocus();
  const target = document.querySelector(`#evidence-body [data-ev="${CSS.escape(id)}"]`);
  if (target && S.focus) target.scrollIntoView({ block: "nearest", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
}
function applyFocus() {
  const c = current();
  const n = c && c.draft && c.draft.narrative;
  let evidenceOn = new Set();
  let sentencesOn = new Set();
  if (S.focus?.sentence && n) {
    const [key, i] = S.focus.sentence.split(":");
    evidenceOn = new Set((n[key][Number(i)] || { citations: [] }).citations);
  }
  if (S.focus?.evidence && n) {
    evidenceOn = new Set([S.focus.evidence]);
    S.lab.sections.forEach(({ key }) => (n[key] || []).forEach((s, i) => { if (s.citations.includes(S.focus.evidence)) sentencesOn.add(`${key}:${i}`); }));
  }
  document.querySelectorAll(".sentence").forEach((node) => {
    node.classList.toggle("focused", S.focus?.sentence === node.dataset.key);
    node.classList.toggle("linked", sentencesOn.has(node.dataset.key));
  });
  document.querySelectorAll("#evidence-body [data-ev]").forEach((node) => node.classList.toggle("on", evidenceOn.has(node.dataset.ev)));
  document.querySelectorAll(".cite[data-ev]").forEach((node) => node.classList.toggle("on", S.focus?.evidence === node.dataset.ev));
}

/* ---------- review ---------- */
let editBuffer = null;
function editingNarrative() {
  const c = current();
  if (S.decision !== "edit") return c.draft.narrative;
  if (!editBuffer || editBuffer.caseId !== c.case_id) {
    const saved = S.local[c.case_id]?.edited_narrative;
    editBuffer = { caseId: c.case_id, narrative: JSON.parse(JSON.stringify(saved || c.draft.narrative)) };
  }
  return editBuffer.narrative;
}
function onEditInput(e) {
  const { key, i, field } = e.target.dataset;
  const s = editBuffer.narrative[key][Number(i)];
  if (field === "text") s.text = e.target.value;
  else s.citations = e.target.value.split(",").map((x) => x.trim()).filter(Boolean);
}

function renderReviewPanel() {
  const c = current();
  const panel = $("#review-panel");
  const committed = S.lab.reviews.rows.find((r) => r.case_id === c.case_id);
  $("#committed-review").replaceChildren(...(committed ? [el("p", { class: "committed" },
    el("strong", { text: `Committed review: ${cap(committed.decision)}` }),
    ` by ${committed.reviewer} on ${day(committed.reviewed_at)}${committed.reason ? `. "${committed.reason}"` : "."}`)] : []));
  const usable = !!(c.draft && c.draft.narrative);
  panel.querySelectorAll(".seg").forEach((b) => { b.disabled = !usable; b.setAttribute("aria-pressed", String(b.dataset.decision === S.decision)); });
  $("#save-review").disabled = !usable || !S.decision;
  $("#cancel-edit").hidden = S.decision !== "edit";
  const fields = $("#review-fields");
  fields.hidden = !(S.decision === "edit" || S.decision === "reject");
  const options = S.decision === "reject" ? S.lab.review_options.reject : S.decision === "edit" ? S.lab.review_options.edit : {};
  const local = S.local[c.case_id];
  $("#reason-code").replaceChildren(el("option", { value: "", text: "Choose a reason" }),
    ...Object.entries(options).map(([k, v]) => el("option", { value: k, text: v, selected: local && local.decision === S.decision && local.reason_code === k })));
  $("#reason-text").value = local && local.decision === S.decision ? local.reason || "" : "";
  const status = $("#review-status");
  status.className = "status";
  status.textContent = local ? `Saved in this browser: ${local.decision}, ${day(local.reviewed_at)}.` : "";
}

function setDecision(decision) {
  S.decision = S.decision === decision ? null : decision;
  if (S.decision !== "edit") editBuffer = null;
  renderNarrative();
  renderReviewPanel();
}

function saveReview() {
  const c = current();
  const status = $("#review-status");
  const reviewer = $("#reviewer-name").value.trim();
  const reasonCode = $("#reason-code").value;
  const reason = $("#reason-text").value.trim();
  const problems = [];
  if (!reviewer) problems.push("add your name");
  if ((S.decision === "reject" || S.decision === "edit") && !reasonCode) problems.push("choose a reason");
  if (S.decision === "reject" && !reason) problems.push("say what is wrong");
  if (problems.length) { status.className = "status error"; status.textContent = `To save, ${problems.join(" and ")}.`; return; }
  const entry = { case_id: c.case_id, decision: S.decision, reason_code: reasonCode || null, reason, reviewer,
    reviewed_at: new Date().toISOString().replace(/\.\d{3}Z$/, "Z"), draft_generated_at: c.draft.generated_at };
  if (S.decision === "edit") {
    const n = editingNarrative();
    const cleaned = {};
    S.lab.sections.forEach(({ key }) => { cleaned[key] = (n[key] || []).filter((s) => s.text.trim()).map((s) => ({ text: s.text.trim(), citations: s.citations })); });
    cleaned.open_questions = c.draft.narrative.open_questions || [];
    entry.edited_narrative = cleaned;
  }
  S.local[c.case_id] = entry;
  try { localStorage.setItem(NAME_KEY, reviewer); } catch { /* storage blocked */ }
  const ok = saveLocal();
  renderQueue();
  renderReviewPanel();
  renderLocalLog();
  status.className = ok ? "status" : "status error";
  status.textContent = ok ? `Saved in this browser: ${S.decision}. Download the log below to keep it.` : "Saved for this visit only; this browser blocks storage.";
}

/* ---------- evaluation and review sections ---------- */
function renderEvaluation() {
  const ev = S.lab.evaluation;
  const kpis = [["Drafts", ev.drafts], ["Pass", ev.pass], ["Pass with warnings", ev.warn], ["Fail", ev.fail], ["Sentences checked", ev.sentences], ["Values checked", ev.values_checked]];
  $("#eval-kpis").replaceChildren(...kpis.map(([k, v]) => {
    const strong = el("strong", { text: "—" });
    if (ev.drafts) countUp(strong, v);
    return el("div", { class: "kpi" }, strong, el("span", { text: k }));
  }));
  $("#check-table").replaceChildren(...Object.values(ev.by_check).map((chk) => {
    const pct = chk.drafts ? Math.round((chk.drafts_passing / chk.drafts) * 100) : 0;
    const bar = el("div", { class: "bar" }, el("i"));
    bar.firstChild.style.width = CAN_ANIMATE ? "0%" : `${pct}%`;
    bar.firstChild.dataset.w = `${pct}%`;
    return el("tr", {}, el("td", { text: chk.label }), el("td", {}, el("span", { class: `pill ${chk.severity === "fail" ? "fail" : "warn"}`, text: chk.severity === "fail" ? "Hard" : "Warn" })),
      el("td", {}, el("div", { class: "bar-cell" }, bar, el("span", { class: "num", text: `${chk.drafts_passing}/${chk.drafts}` }))), el("td", { class: "num", text: num(chk.issues) }));
  }));
  whenVisible($("#check-table"), () => document.querySelectorAll("#check-table .bar i").forEach((i) => { i.style.width = i.dataset.w; }), 0.1);
  const f = S.lab.faults;
  $("#fault-count").textContent = f.planted ? `${f.caught} of ${f.planted} caught` : "Not run yet";
  $("#fault-table").replaceChildren(...Object.values(f.by_fault).map((x) => el("tr", {},
    el("td", { text: x.label }), el("td", { class: "num", text: x.planted }), el("td", { class: "num", text: x.planted ? `${x.caught} (${Math.round((x.caught / x.planted) * 100)}%)` : "—" }))));
}

function renderReviews() {
  const r = S.lab.reviews;
  const opts = S.lab.review_options;
  $("#review-count").textContent = `${r.reviewed} of ${S.lab.meta.drafts}`;
  if (!r.reviewed) {
    $("#review-outcomes").replaceChildren(el("p", { class: "muted", text: "No reviews committed yet. Reviews made on this page stay in your browser until the log is downloaded and committed." }));
    $("#review-table").replaceChildren();
    return;
  }
  $("#review-outcomes").replaceChildren(el("div", { class: "outcomes" }, Object.entries(opts.outcomes).map(([k, label]) =>
    el("div", { class: k === "agree" ? "" : r.outcomes[k] ? "hot" : "" }, el("strong", { text: r.outcomes[k] }), el("span", { text: label })))));
  $("#review-table").replaceChildren(...r.rows.map((row) => el("tr", {},
    el("td", { text: row.case_id }), el("td", { text: cap(row.decision) }),
    el("td", { text: row.reason_code ? `${(opts[row.decision] || {})[row.reason_code] || row.reason_code}${row.reason ? `: ${row.reason}` : ""}` : row.reason || "" }),
    el("td", {}, pill(row.evaluator_verdict)), el("td", { text: opts.outcomes[row.outcome] }))));
}

function renderLocalLog() {
  const entries = Object.values(S.local);
  $("#local-count").textContent = `${entries.length} saved`;
  const counts = { accept: 0, edit: 0, reject: 0 };
  entries.forEach((e) => { counts[e.decision] += 1; });
  $("#local-summary").textContent = entries.length
    ? `${counts.accept} accepted, ${counts.edit} edited, ${counts.reject} rejected. Only you can see these until you download and commit them.`
    : "Review a draft above and it will appear here.";
  $("#download-log").disabled = !entries.length;
  $("#clear-log").disabled = !entries.length;
}

function downloadLog() {
  const blob = new Blob([JSON.stringify({ reviews: Object.values(S.local) }, null, 1)], { type: "application/json" });
  const a = el("a", { href: URL.createObjectURL(blob), download: "sar-review-log.json" });
  document.body.append(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 0);
}

/* ---------- start ---------- */
async function start() {
  loadLocal();
  try {
    const res = await fetch("data/lab.json", { cache: "no-store" });
    if (!res.ok) throw new Error(res.status);
    S.lab = await res.json();
  } catch {
    $("#narrative-body").replaceChildren(el("p", { class: "empty", text: "Could not load data/lab.json. Serve the dist folder over http (python3 -m http.server --directory dist)." }));
    return;
  }
  S.lab.cases.forEach((c) => { S.byId[c.case_id] = c; });
  renderHero();
  renderEvaluation();
  renderReviews();
  renderLocalLog();
  document.querySelectorAll(".filters .chip").forEach((b) => b.addEventListener("click", () => {
    S.filter = b.dataset.filter;
    document.querySelectorAll(".filters .chip").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    const first = visibleCases()[0];
    if (first && !visibleCases().some((c) => c.case_id === S.caseId)) selectCase(first.case_id); else renderQueue();
  }));
  $("#case-select").addEventListener("change", (e) => selectCase(e.target.value));
  document.querySelectorAll(".seg").forEach((b) => b.addEventListener("click", () => setDecision(b.dataset.decision)));
  $("#save-review").addEventListener("click", saveReview);
  $("#cancel-edit").addEventListener("click", () => setDecision("edit"));
  $("#download-log").addEventListener("click", downloadLog);
  $("#clear-log").addEventListener("click", () => {
    if (!confirm("Remove every review saved in this browser?")) return;
    S.local = {};
    saveLocal();
    renderQueue(); renderReviewPanel(); renderLocalLog();
  });
  const fromHash = location.hash.match(/CASE-\d{4}/);
  const firstDrafted = S.lab.cases.find((c) => c.draft && c.draft.narrative) || S.lab.cases[0];
  selectCase(fromHash && S.byId[fromHash[0]] ? fromHash[0] : firstDrafted.case_id);
  whenVisible($("#narrative-body"), () => { if (!typedOnce) typeNarrative(); }, 0.2);
}

start();
