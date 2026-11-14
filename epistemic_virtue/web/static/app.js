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

function summaryTiles(summary, directions) {
  const tiles = [];
  for (const [name, stats] of Object.entries(summary.metrics)) {
    tiles.push(tile(name, stats.mean, summary.baseline?.metrics[name].mean, directions[name]));
  }
  if (summary.pair_consistency) {
    const pc = summary.pair_consistency;
    const base = summary.baseline ? summary.baseline.pair_consistency?.rate : undefined;
    tiles.push(tile(`pair_consistency (${pc.consistent}/${pc.pairs})`, pc.rate, base, "higher"));
  }
  if (summary.guardian) {
    const g = summary.guardian;
    tiles.push(el("div", { class: "tile" },
      el("div", { class: "name", text: "guardian" }),
      el("div", { class: "value", text: `${g.approved}/${summary.n}` }),
      el("div", { class: "delta", text: `approved · ${g.revised} revised · ${g.rejected} rejected · ${g.errors} errors` })));
  }
  return tiles;
}

function renderSummary(summary, directions) {
  $("eval-metrics").replaceChildren(...summaryTiles(summary, directions));
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
      } else if (ev.type === "saved") {
        $("eval-status").append(` Saved as run ${ev.run_id}; see the Runs tab.`);
      }
    });
  } catch (error) {
    $("eval-status").textContent = `Run failed: ${error.message}`;
  } finally {
    button.disabled = false;
  }
}

/* ------------------------------------------------------------------ runs */

const selectedRuns = new Set();
let runOrder = []; // run ids, newest first

async function getJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json();
}

function runStatus(run) {
  const kind = { completed: "pass", failed: "fail" }[run.status] ?? "neutral";
  return el("span", { class: `badge ${kind}`, text: run.status.toUpperCase() });
}

function metricLine(summary) {
  return Object.entries(summary.metrics).map(([name, stats]) => `${name} ${fmt(stats.mean)}`).join(" · ");
}

function syncRunSelection() {
  $("runs-compare").disabled = selectedRuns.size !== 2;
}

function runRow(run) {
  const config = run.config;
  const checkbox = el("input", { type: "checkbox", "aria-label": `Select run ${run.run_id} for comparison` });
  checkbox.checked = selectedRuns.has(run.run_id);
  checkbox.addEventListener("change", () => {
    if (checkbox.checked) selectedRuns.add(run.run_id); else selectedRuns.delete(run.run_id);
    syncRunSelection();
  });
  return el("tr", {},
    el("td", {}, checkbox),
    el("td", {}, el("button", { class: "link", type: "button", text: run.started_at.replace("T", " ").slice(0, 19),
      onclick: () => showRun(run.run_id) })),
    el("td", {}, config.task, el("div", { class: "muted", text: `${config.dataset} items` })),
    el("td", { class: "id", text: config.model }),
    el("td", { class: "id", text: config.guardian_model ?? "–" }),
    el("td", { class: "num", text: String(run.n_items) }),
    el("td", { class: "wrap-ok", text: metricLine(run.summary) }),
    el("td", {}, runStatus(run)));
}

async function loadRuns() {
  const table = $("runs-table");
  try {
    const runs = await getJson("/api/runs");
    runOrder = runs.map((run) => run.run_id);
    const known = new Set(runOrder);
    for (const id of [...selectedRuns]) if (!known.has(id)) selectedRuns.delete(id);
    table.querySelector("tbody").replaceChildren(...runs.map(runRow));
    table.hidden = runs.length === 0;
    $("runs-empty").hidden = runs.length > 0;
    $("runs-status").textContent = runs.length
      ? `${runs.length} saved run${runs.length > 1 ? "s" : ""}. Tick two runs of the same task to compare them.`
      : "Every evaluation is saved here with its settings, provenance and item-level results.";
  } catch (error) {
    $("runs-status").textContent = `Could not load runs: ${error.message}`;
  }
  syncRunSelection();
}

function facts(pairs) {
  const list = el("dl", { class: "facts" });
  for (const [term, value, mono] of pairs) {
    if (value == null || value === "") continue;
    list.append(el("dt", { text: term }), el("dd", {}, mono ? el("code", { text: String(value) }) : String(value)));
  }
  return list;
}

function usageText(usage) {
  if (!usage) return null;
  const tokens = usage.input_tokens == null ? "tokens not reported"
    : `${usage.input_tokens} input and ${usage.output_tokens} output tokens`;
  return `${usage.calls} calls, ${tokens}`;
}

function useSettings(config) {
  $("eval-task").value = config.task;
  $("eval-model").value = config.model;
  $("eval-limit").value = config.limit ?? "";
  $("eval-gate").checked = Boolean(config.guardian_model);
  if (config.guardian_model) $("eval-guardian-model").value = config.guardian_model;
  $("eval-max-revisions").value = config.max_revisions;
  syncEvalForm();
  $("eval-dataset").value = config.dataset;
  syncEvalForm();
  selectTab($("tab-eval"));
  $("eval-status").textContent = "Settings loaded from a saved run. Press Run task to repeat it.";
}

async function deleteRun(runId) {
  if (!window.confirm(`Delete run ${runId}? This removes its file and cannot be undone.`)) return;
  const response = await fetch(`/api/runs/${runId}`, { method: "DELETE" });
  if (!response.ok) {
    $("runs-status").textContent = `Could not delete: ${await errorMessage(response)}`;
    return;
  }
  $("run-view").replaceChildren();
  await loadRuns();
}

async function showRun(runId) {
  const view = $("run-view");
  let run;
  try {
    run = await getJson(`/api/runs/${runId}`);
  } catch (error) {
    view.replaceChildren(el("p", { class: "hint warn", text: `Could not load run: ${error.message}` }));
    return;
  }
  const config = run.config, prov = run.provenance;
  const commit = prov.git_commit ? `${prov.git_commit.slice(0, 12)}${prov.git_dirty ? " (uncommitted changes)" : ""}` : null;

  const table = el("table", {},
    el("thead", {}, el("tr", {}, ...["Item", "Answer", "Score", "Guardian"].map((text) => el("th", { text })))),
    el("tbody", {}, ...run.items.flatMap(itemRows)));

  view.replaceChildren(
    el("div", { class: "card" },
      el("div", { class: "toolbar" },
        el("h2", { text: `Run ${run.run_id}` }),
        el("div", { class: "toolbar-actions" },
          el("button", { class: "secondary", type: "button", text: "Use these settings", onclick: () => useSettings(config) }),
          el("a", { class: "secondary", href: `/api/runs/${run.run_id}`, download: `${run.run_id}.json`, text: "Download JSON" }),
          el("button", { class: "secondary danger", type: "button", text: "Delete", onclick: () => deleteRun(run.run_id) }))),
      run.error ? el("p", { class: "hint warn", text: `${run.status}: ${run.error} Results cover only the ${run.items.length} items that finished.` }) : null,
      facts([
        ["Status", run.status],
        ["Started", `${run.started_at} (${run.duration_seconds} s)`],
        ["Task", `${config.task}, ${config.dataset} items${config.limit ? `, limit ${config.limit}` : ""}`],
        ["Model", config.model, true],
        ["Guardian", config.guardian_model ? `${config.guardian_model}, up to ${config.max_revisions} revisions` : "off"],
        ["Model usage", usageText(run.usage.actor)],
        ["Guardian usage", usageText(run.usage.guardian)],
        ["Generation settings", prov.generation_settings],
        ["Code commit", commit, true],
        ["Versions", `package ${prov.package_version}, inspect-ai ${prov.inspect_ai_version}, Python ${prov.python_version}`],
        ["Items SHA-256", prov.items_sha256, true],
        ["System prompt SHA-256", prov.system_prompt_sha256, true],
        ["Guardian prompt SHA-256", prov.guardian_prompt_sha256, true],
      ])),
    el("div", { class: "tiles" }, ...summaryTiles(run.summary, run.metric_directions)),
    el("div", { class: "table-scroll" }, table));
  view.scrollIntoView({ behavior: "smooth", block: "start" });
}

function deltaCell(metric) {
  if (metric.delta == null) return el("td", { class: "num", text: "–" });
  let cls = "num";
  if (metric.delta !== 0 && metric.direction !== "neutral") {
    cls += (metric.direction === "lower" ? metric.delta < 0 : metric.delta > 0) ? " better" : " worse";
  }
  return el("td", { class: cls, text: `${metric.delta > 0 ? "+" : ""}${metric.delta.toFixed(3)}` });
}

function runLabel(side) {
  const config = side.config;
  const guardian = config.guardian_model ? ` + guardian ${config.guardian_model}` : "";
  return `${config.model}${guardian} (${config.dataset}, ${side.run_id.slice(0, 16)})`;
}

async function compareRuns() {
  const view = $("run-view");
  // A is the older run and B the newer, so "B − A" reads as the change over time.
  const [b, a] = runOrder.filter((id) => selectedRuns.has(id));
  let result;
  try {
    result = await getJson(`/api/runs/compare?a=${a}&b=${b}`);
  } catch (error) {
    view.replaceChildren(el("p", { class: "hint warn", text: `Cannot compare: ${error.message}` }));
    return;
  }

  const notes = [];
  if (!result.same_items) {
    notes.push(`The runs did not use the same items: ${result.shared_items} shared, ${result.only_a} only in A, ` +
      `${result.only_b} only in B. The averages below are over each run's own items, so treat the differences with care.`);
  }
  for (const side of [result.a, result.b]) {
    if (side.status !== "completed") notes.push(`Run ${side.run_id} is ${side.status}, so its results are partial.`);
  }

  const metrics = el("table", {},
    el("thead", {}, el("tr", {}, ...["Metric", "A", "B", "B − A"].map((text) => el("th", { text })))),
    el("tbody", {}, ...Object.entries(result.metrics).map(([name, metric]) => el("tr", {},
      el("td", { text: `${name} (${metric.direction === "neutral" ? "no better direction" : metric.direction + " is better"})` }),
      el("td", { class: "num", text: fmt(metric.a) }),
      el("td", { class: "num", text: fmt(metric.b) }),
      deltaCell(metric)))));

  const scoreText = (side) => Object.entries(side.metrics).map(([k, v]) => `${k} ${fmt(v)}`).join(" · ");
  const differing = result.differing_items.length
    ? el("table", {},
        el("thead", {}, el("tr", {}, ...["Item", "A", "B"].map((text) => el("th", { text })))),
        el("tbody", {}, ...result.differing_items.map((item) => el("tr", {},
          el("td", { class: "id", text: item.id }),
          el("td", {}, item.a.answer ?? "–", el("div", { class: "muted", text: scoreText(item.a) })),
          el("td", {}, item.b.answer ?? "–", el("div", { class: "muted", text: scoreText(item.b) }))))))
    : el("p", { class: "hint", text: "Every shared item scored the same in both runs." });

  view.replaceChildren(
    el("div", { class: "card" },
      el("h2", { text: `Comparison: ${result.task}` }),
      facts([["A", runLabel(result.a), true], ["B", runLabel(result.b), true]]),
      ...notes.map((text) => el("p", { class: "hint warn", text }))),
    el("div", { class: "table-scroll" }, metrics),
    el("h2", { text: `Items scored differently (${result.differing_items.length} of ${result.shared_items} shared)` }),
    el("div", { class: "table-scroll" }, differing),
    el("p", { class: "hint", text: "Differences are raw, with no significance test. A single pair of runs cannot separate a real effect from run-to-run variation." }));
  view.scrollIntoView({ behavior: "smooth", block: "start" });
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
  $("runs-refresh").addEventListener("click", loadRuns);
  $("runs-compare").addEventListener("click", compareRuns);
  $("tab-runs").addEventListener("click", loadRuns);
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
