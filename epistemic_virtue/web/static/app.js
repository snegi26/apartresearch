"use strict";

// Model output is untrusted: everything is rendered with textContent, never innerHTML.

const $ = (id) => document.getElementById(id);

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  node.append(...children.filter((child) => child != null));
  return node;
}

async function errorMessage(response) {
  try {
    const body = await response.json();
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) {
      return body.detail.map((d) => `${d.loc.slice(1).join(".")}: ${d.msg}`).join("; ");
    }
  } catch { /* fall through */ }
  return `Request failed (${response.status})`;
}

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(await errorMessage(response));
  return response;
}

/** POST and call onEvent for each newline-delimited JSON event in the response. */
async function streamEvents(url, payload, onEvent) {
  const response = await postJson(url, payload);
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    const lines = buffer.split("\n");
    buffer = lines.pop();
    for (const line of lines) {
      if (!line.trim()) continue;
      const event = JSON.parse(line);
      if (event.type === "error") throw new Error(event.message);
      onEvent(event);
    }
  }
}

function badge(status) {
  const kind = status === "PASS" ? "pass" : status === "FAIL" ? "fail" : "neutral";
  return el("span", { class: `badge ${kind}`, text: status });
}

/* ------------------------------------------------------------------ tabs */

const tabs = [...document.querySelectorAll('[role="tab"]')];

function selectTab(tab) {
  for (const other of tabs) {
    const selected = other === tab;
    other.setAttribute("aria-selected", String(selected));
    other.tabIndex = selected ? 0 : -1;
    $(other.getAttribute("aria-controls")).hidden = !selected;
  }
}

tabs.forEach((tab, index) => {
  tab.addEventListener("click", () => selectTab(tab));
  tab.addEventListener("keydown", (event) => {
    const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
    if (!step) return;
    const next = tabs[(index + step + tabs.length) % tabs.length];
    selectTab(next);
    next.focus();
  });
});

/* -------------------------------------------------------------- guardian */

const OUTCOMES = {
  approved: ["pass", "Approved", "The Guardian passed this response."],
  rejected: ["fail", "Not approved", "The revision limit was reached without a PASS. Do not use the last draft as an answer."],
  guardian_error: ["error", "Guardian error", "The Guardian did not return a valid verdict, so nothing was approved."],
};

function currentMode() {
  return document.querySelector('input[name="mode"]:checked').value;
}

function syncMode() {
  const review = currentMode() === "review";
  $("draft-field").hidden = !review;
  $("draft").required = review;
  $("actor-field").hidden = review;
  $("revisions-field").hidden = review;
  $("guardian-submit").textContent = review ? "Review draft" : "Run";
  syncSameModelWarning();
}

function syncSameModelWarning() {
  const same = $("actor-model").value.trim() === $("guardian-model").value.trim();
  $("same-model-warning").hidden = currentMode() === "review" || !same;
}

function showOutcome(kind, title, detail) {
  const box = $("guardian-outcome");
  box.className = `outcome ${kind}`;
  box.replaceChildren(title, el("small", { text: detail }));
  box.hidden = false;
}

function draftStep(round, text, label) {
  return el("li", { class: "step" },
    el("div", { class: "step-head" }, el("strong", { text: label }), el("span", { text: `Round ${round}` })),
    el("div", { class: "step-body" }, el("p", { text })));
}

function verdictStep(event) {
  const verdict = event.verdict;
  const body = el("div", { class: "step-body" });
  if (verdict) {
    body.append(
      el("div", { class: "sub", text: "Chain of thought" }),
      el("p", { text: verdict.chain_of_thought }),
      el("div", { class: "sub", text: "Revision directives" }),
      el("p", { text: verdict.revision_directives }),
    );
  } else {
    body.append(el("p", { text: event.error }), el("div", { class: "sub", text: "Raw reply" }), el("pre", { text: event.raw }));
  }
  return el("li", { class: `step ${verdict?.status === "PASS" ? "verdict-pass" : "verdict-fail"}` },
    el("div", { class: "step-head" },
      el("strong", { text: "Guardian verdict" }),
      badge(verdict ? verdict.status : "INVALID")),
    body);
}

async function submitGuardian(event) {
  event.preventDefault();
  const button = $("guardian-submit");
  const timeline = $("timeline");
  const base = {
    intent: $("intent").value,
    context: $("context").value,
    guardian_model: $("guardian-model").value.trim(),
  };
  timeline.replaceChildren();
  $("guardian-outcome").hidden = true;
  $("guardian-empty").hidden = true;
  button.disabled = true;

  try {
    if (currentMode() === "review") {
      const draft = $("draft").value;
      const response = await postJson("/api/guardian/review", { ...base, draft });
      const result = await response.json();
      timeline.append(draftStep(1, draft, "Submitted draft"), verdictStep(result));
      const outcome = !result.verdict ? "guardian_error" : result.verdict.status === "PASS" ? "approved" : null;
      if (outcome) showOutcome(...OUTCOMES[outcome]);
      else showOutcome("fail", "Rejected", "The draft violates at least one maxim. See the directives below.");
    } else {
      const payload = {
        ...base,
        actor_model: $("actor-model").value.trim(),
        max_revisions: Number($("max-revisions").value),
      };
      await streamEvents("/api/guardian/run", payload, (ev) => {
        if (ev.type === "draft") {
          timeline.append(draftStep(ev.round, ev.text, ev.round === 1 ? "Actor draft" : "Actor revision"));
        } else if (ev.type === "verdict") {
          timeline.append(verdictStep(ev));
        } else if (ev.type === "final") {
          const [kind, title, detail] = OUTCOMES[ev.outcome];
          showOutcome(kind, title, `${detail} Rounds: ${ev.rounds}.`);
        }
      });
    }
  } catch (error) {
    showOutcome("error", "Run failed", error.message);
  } finally {
    button.disabled = false;
  }
}

function applyPreset(preset) {
  $("intent").value = preset.intent;
  $("context").value = preset.context;
  $("draft").value = preset.draft;
}

/* ------------------------------------------------------------ evaluation */

const fmt = (value) => (value == null ? "–" : value.toFixed(3));
let tasksByName = {};
let demoActor = "";

function syncEvalForm() {
  const task = tasksByName[$("eval-task").value];
  $("eval-gate-options").hidden = !$("eval-gate").checked;
  if (!task) return;

  const select = $("eval-dataset");
  const hasGenerated = task.items.generated > 0;
  for (const option of select.options) option.disabled = option.value !== "seed" && !hasGenerated;
  if (!hasGenerated) select.value = "seed";

  const generated = hasGenerated ? `${task.items.generated} generated items` : "no generated items";
  $("eval-task-description").textContent =
    `${task.description} ${task.items.seed} seed items, ${generated}.`;
  $("eval-demo-warning").hidden =
    select.value === "seed" || $("eval-model").value.trim() !== demoActor;
}

function tile(name, value, baseline, direction) {
  const node = el("div", { class: "tile" },
    el("div", { class: "name", text: name }),
    el("div", { class: "value", text: fmt(value) }));
  if (baseline !== undefined) {
    let cls = "delta";
    if (value != null && baseline != null && value !== baseline && direction !== "neutral") {
      const improved = direction === "lower" ? value < baseline : value > baseline;
      cls += improved ? " better" : " worse";
    }
    node.append(el("div", { class: cls, text: `first draft ${fmt(baseline)}` }));
  }
  return node;
}

function renderSummary(summary, directions) {
  const tiles = $("eval-metrics");
  tiles.replaceChildren();
  for (const [name, stats] of Object.entries(summary.metrics)) {
    tiles.append(tile(name, stats.mean, summary.baseline?.metrics[name].mean, directions[name]));
  }
  if (summary.pair_consistency) {
    const pc = summary.pair_consistency;
    const base = summary.baseline ? summary.baseline.pair_consistency?.rate : undefined;
    tiles.append(tile(`pair_consistency (${pc.consistent}/${pc.pairs})`, pc.rate, base, "higher"));
  }
  if (summary.guardian) {
    const g = summary.guardian;
    tiles.append(el("div", { class: "tile" },
      el("div", { class: "name", text: "guardian" }),
      el("div", { class: "value", text: `${g.approved}/${summary.n}` }),
      el("div", { class: "delta", text: `approved · ${g.revised} revised · ${g.rejected} rejected · ${g.errors} errors` })));
  }
  const unscored = summary.unscored ? ` ${summary.unscored} could not be scored.` : "";
  $("eval-status").textContent = `Finished ${summary.n} items.${unscored}`;
}

function detailBlock(label, text) {
  return el("div", {}, el("div", { class: "sub", text: label }), el("pre", { text }));
}

function itemRows(item) {
  const score = item.score;
  const metrics = Object.entries(score.metrics).map(([k, v]) => `${k} ${fmt(v)}`).join(" · ");
  const guardian = item.guardian;

  const detail = el("tr", { class: "detail", hidden: "" }, el("td", { colspan: "4" },
    el("div", { class: "grid" },
      detailBlock("Prompt", item.input),
      detailBlock("Gold target", item.target),
      guardian && guardian.rounds > 1 ? detailBlock("First draft", guardian.first_draft) : null,
      detailBlock("Final response", item.response),
      ...(guardian ? guardian.reviews.map((r) => detailBlock(
        `Guardian round ${r.round}: ${r.verdict ? r.verdict.status : "INVALID"}`,
        r.verdict ? `${r.verdict.chain_of_thought}\n\nDirectives: ${r.verdict.revision_directives}` : r.error)) : []),
    )));

  const toggle = el("button", { class: "link", type: "button", text: "details", "aria-expanded": "false",
    onclick: () => {
      detail.hidden = !detail.hidden;
      toggle.setAttribute("aria-expanded", String(!detail.hidden));
    } });

  let guardianCell = el("span", { class: "muted", text: "off" });
  if (guardian) {
    const status = { approved: "PASS", rejected: "FAIL", guardian_error: "INVALID" }[guardian.outcome];
    guardianCell = el("span", {}, badge(status),
      el("span", { class: "muted", text: ` ${guardian.rounds} round${guardian.rounds > 1 ? "s" : ""}` }));
  }

  const main = el("tr", {},
    el("td", { class: "id" }, item.id, el("br"), toggle),
    el("td", {}, score.answer ?? "–", el("div", { class: "muted", text: score.error ?? score.explanation })),
    el("td", {}, metrics,
      item.baseline_score ? el("div", { class: "muted",
        text: "first draft: " + Object.entries(item.baseline_score.metrics).map(([k, v]) => `${k} ${fmt(v)}`).join(" · ") }) : null),
    el("td", {}, guardianCell));
  return [main, detail];
}

async function submitEval(event) {
  event.preventDefault();
  const button = $("eval-submit");
  const table = $("eval-table");
  const tbody = table.querySelector("tbody");
  const gated = $("eval-gate").checked;
  const limit = $("eval-limit").value;
  const payload = {
    task: $("eval-task").value,
    dataset: $("eval-dataset").value,
    model: $("eval-model").value.trim(),
    guardian_model: gated ? $("eval-guardian-model").value.trim() : null,
    limit: limit ? Number(limit) : null,
    max_revisions: Number($("eval-max-revisions").value),
  };

  tbody.replaceChildren();
  $("eval-metrics").replaceChildren();
  table.hidden = true;
  $("eval-caveat").hidden = true;
  $("eval-status").textContent = "Starting…";
  button.disabled = true;
  let total = 0, done = 0, directions = {};

  try {
    await streamEvents("/api/eval/run", payload, (ev) => {
      if (ev.type === "start") {
        total = ev.total;
        directions = ev.metrics;
        table.hidden = false;
      } else if (ev.type === "item") {
        tbody.append(...itemRows(ev));
        $("eval-status").textContent = `Scored ${++done} of ${total}…`;
      } else if (ev.type === "summary") {
        renderSummary(ev, directions);
        $("eval-caveat").hidden = false;
      }
    });
  } catch (error) {
    $("eval-status").textContent = `Run failed: ${error.message}`;
  } finally {
    button.disabled = false;
  }
}

/* ------------------------------------------------------------------ init */

async function init() {
  const config = await (await fetch("/api/config")).json();

  const suggestions = [config.demo.actor, config.demo.guardian,
    "anthropic/claude-sonnet-5-5", "anthropic/claude-opus-5-5", "anthropic/claude-haiku-5-5"];
  $("model-list").append(...suggestions.map((value) => el("option", { value })));
  $("actor-model").value = config.demo.actor;
  $("eval-model").value = config.demo.actor;
  $("guardian-model").value = config.demo.guardian;
  $("eval-guardian-model").value = config.demo.guardian;
  for (const id of ["max-revisions", "eval-max-revisions"]) $(id).max = config.max_revisions_limit;

  const configured = Object.entries(config.providers).filter(([, ok]) => ok).map(([name]) => name);
  const note = $("demo-note");
  note.textContent = configured.length
    ? `Provider keys found for: ${configured.join(", ")}. Enter a model as provider/model-id. The demo/ models are offline and rule-based.`
    : "No provider API keys found, so the defaults are the offline demo models: a scripted Actor and a rule-based Guardian. " +
      `They show the workflow but do not judge like a model. Set ${Object.values(config.provider_keys).join(" or ")} and restart to use real models.`;
  note.hidden = false;

  $("presets").append(...config.presets.map((preset) =>
    el("button", { type: "button", text: preset.label, title: `Expected verdict on the sample draft: ${preset.expects}`,
      onclick: () => applyPreset(preset) })));
  applyPreset(config.presets[0]);

  tasksByName = Object.fromEntries(config.tasks.map((task) => [task.name, task]));
  $("eval-task").append(...config.tasks.map((task) => el("option", { value: task.name, text: task.title })));
  $("guardian-prompt").textContent = config.guardian_prompt;

  document.querySelectorAll('input[name="mode"]').forEach((input) => input.addEventListener("change", syncMode));
  for (const id of ["actor-model", "guardian-model"]) $(id).addEventListener("input", syncSameModelWarning);
  demoActor = config.demo.actor;
  for (const id of ["eval-task", "eval-dataset"]) $(id).addEventListener("change", syncEvalForm);
  $("eval-model").addEventListener("input", syncEvalForm);
  $("eval-gate").addEventListener("change", syncEvalForm);
  $("guardian-form").addEventListener("submit", submitGuardian);
  $("eval-form").addEventListener("submit", submitEval);
  syncMode();
  syncEvalForm();
}

init().catch((error) => {
  const note = $("demo-note");
  note.textContent = `Could not load the console: ${error.message}`;
  note.hidden = false;
});
