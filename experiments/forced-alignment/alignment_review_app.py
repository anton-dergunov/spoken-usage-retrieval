"""A self-contained local page for the blind karaoke review.

Three timing systems play against the same clip under the labels A, B and C, in an order
randomised per clip. Which label is which model is withheld until the row is submitted, for
the same reason the caption-reliability review withholds its ASR text: a reviewer who knows
they are listening to "the big model" hears it more kindly.

Audio only, deliberately. With video the reviewer can lip-read and can see burnt-in
subtitles, both of which supply timing the alignment did not.

The page opens straight from disk: clips are embedded as data URIs, judgements live in the
browser's local storage, and the filled worksheet downloads as JSON for ``review-import``.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from alignment_eval import SYNC_RATINGS, SYNC_TAGS

MAX_EMBEDDED_BYTES = 64 * 1024 * 1024

GUIDE = """
<h2>How to judge one clip</h2>
<ol>
  <li><strong>Press play and watch the highlight.</strong> Each of A, B and C is the same
      words and the same audio, timed by a different system. Replay as often as you like.</li>
  <li><strong>Judge synchronisation only.</strong> Not whether the transcript is correct, not
      whether the clip is cut well &mdash; only whether the highlight lands on each word as
      you hear it.</li>
  <li><strong>Rate all three</strong> before moving on. They are shown in a different order on
      every clip, and which is which stays hidden until you submit the row, so a
      well-known model cannot flatter itself.</li>
  <li><strong>Expect one of them to be bad.</strong> One of the three is a no-model baseline
      that spreads the words evenly across the clip. If you cannot tell it apart from the
      others, that is the single most useful result this review can produce &mdash; it would
      mean the models are not earning their cost.</li>
  <li><strong>Tag what is wrong</strong> when a system is not in sync. Empty tags are fine
      for anything you rated in sync.</li>
  <li>Some clips repeat later with the systems shuffled differently. That is deliberate: it
      measures how consistent your own judgements are, which is the ceiling for every
      agreement number in the report. Just judge them as you find them.</li>
</ol>
"""


def audio_data_uri(path: Path) -> str | None:
    try:
        payload = path.read_bytes()
    except OSError:
        return None
    return "data:audio/wav;base64," + base64.b64encode(payload).decode("ascii")


def collect_audio(worksheet: dict[str, Any]) -> tuple[dict[str, str], int]:
    """Embed each distinct clip once, within the size budget."""
    audio: dict[str, str] = {}
    total = 0
    for item in worksheet.get("items", []):
        raw = item.get("clip")
        segment = item["segment_id"]
        if not raw or segment in audio:
            continue
        path = Path(raw)
        if not path.is_file():
            continue
        size = path.stat().st_size
        if total + size > MAX_EMBEDDED_BYTES:
            continue
        encoded = audio_data_uri(path)
        if encoded is None:
            continue
        audio[segment] = encoded
        total += size
    return audio, total


def timing_payload(rows: dict[str, Any], worksheet: dict[str, Any]) -> dict[str, Any]:
    """Per review item, the timed word groups for each blind label.

    Only ``matched`` groups carry times; the page renders everything else as plain text, so
    an unmatched word is visibly never highlighted rather than being quietly skipped.
    """
    payload: dict[str, Any] = {}
    for item in worksheet.get("items", []):
        row = rows.get(item["segment_id"])
        if row is None:
            continue
        per_label: dict[str, list[dict[str, Any]]] = {}
        for label, system in item["assignment"].items():
            result = row.system(system)
            if result is None:
                continue
            per_label[label] = [
                {"text": pair.text, "start": pair.aligned_start} for pair in result.comparisons
            ]
        payload[item["review_id"]] = per_label
    return payload


def _script_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")


def render_review_app(
    worksheet: dict[str, Any],
    rows: dict[str, Any],
    *,
    embed_audio: bool = True,
) -> str:
    audio: dict[str, str] = {}
    embedded_bytes = 0
    if embed_audio:
        audio, embedded_bytes = collect_audio(worksheet)
    payload = {
        "worksheet": worksheet,
        "audio": audio,
        "timing": timing_payload(rows, worksheet),
        "embeddedBytes": embedded_bytes,
        "ratings": [
            {"id": value, "label": label, "anchor": anchor} for value, label, anchor in SYNC_RATINGS
        ],
        "tags": [{"id": key, "anchor": anchor} for key, anchor in SYNC_TAGS.items()],
    }
    return _TEMPLATE.replace("__PAYLOAD__", _script_json(payload)).replace("__GUIDE__", GUIDE)


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Alignment sync review</title>
<style>
  :root {
    --bg: #f7f6f3; --card: #ffffff; --ink: #1c1b19; --muted: #6b6864;
    --line: #dedad3; --accent: #7a5c2e; --warn: #8a4b2a; --ok: #3f6b45;
    --spoken: #2f5d8a; --spoken-bg: #dbe9f6;
    color-scheme: light dark;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #171614; --card: #201f1c; --ink: #ece9e3; --muted: #a09b93;
      --line: #34322d; --accent: #d4b483; --warn: #e0a080; --ok: #8fc396;
      --spoken: #9fc7ec; --spoken-bg: #22364a;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--ink);
    font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }
  header {
    position: sticky; top: 0; z-index: 5; background: var(--card);
    border-bottom: 1px solid var(--line); padding: 12px 20px;
    display: flex; gap: 16px; align-items: center; flex-wrap: wrap;
  }
  h1 { font-size: 16px; margin: 0; font-weight: 650; }
  .muted { color: var(--muted); }
  .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
  main { max-width: 920px; margin: 0 auto; padding: 20px; }
  details.guide {
    background: var(--card); border: 1px solid var(--line); border-radius: 10px;
    padding: 12px 16px; margin-bottom: 20px;
  }
  details.guide summary { cursor: pointer; font-weight: 600; }
  details.guide h2 { font-size: 15px; margin: 14px 0 6px; }
  details.guide ol { margin: 0 0 8px; padding-left: 20px; }
  details.guide li { margin-bottom: 5px; }
  .card {
    background: var(--card); border: 1px solid var(--line); border-radius: 10px;
    padding: 16px 18px; margin-bottom: 18px;
  }
  .card.done { border-color: var(--ok); }
  .row { display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; }
  .badge {
    font-size: 11px; letter-spacing: .02em; text-transform: uppercase;
    border: 1px solid var(--line); border-radius: 999px; padding: 2px 8px; color: var(--muted);
  }
  audio { width: 100%; margin: 12px 0 4px; }
  .system { border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; margin: 12px 0; }
  .system h4 {
    margin: 0 0 8px; font-size: 12px; text-transform: uppercase;
    letter-spacing: .06em; color: var(--muted); font-weight: 700;
  }
  .karaoke { margin: 0 0 10px; font-size: 17px; line-height: 1.7; }
  .karaoke .w { transition: color .08s linear, background-color .08s linear; border-radius: 3px; }
  .karaoke .w.on { color: var(--spoken); background: var(--spoken-bg); font-weight: 600; }
  .karaoke .w.untimed { color: var(--muted); font-style: italic; }
  .choices { display: flex; gap: 6px; flex-wrap: wrap; }
  label.opt {
    display: inline-flex; gap: 6px; align-items: center; padding: 5px 10px;
    border: 1px solid var(--line); border-radius: 999px; cursor: pointer; font-size: 13px;
  }
  label.opt:hover { background: color-mix(in srgb, var(--accent) 10%, transparent); }
  label.opt input { margin: 0; }
  .tags { margin-top: 8px; display: flex; gap: 6px; flex-wrap: wrap; }
  .tags label { font-size: 12px; }
  fieldset { border: 0; padding: 0; margin: 12px 0 0; }
  legend { font-size: 12px; font-weight: 650; padding: 0; margin-bottom: 6px; }
  textarea {
    width: 100%; min-height: 44px; margin-top: 6px; padding: 8px; border-radius: 6px;
    border: 1px solid var(--line); background: transparent; color: inherit;
    font: inherit; resize: vertical;
  }
  input[type=text] {
    padding: 5px 8px; border: 1px solid var(--line); border-radius: 6px;
    background: transparent; color: inherit; font: inherit;
  }
  button {
    font: inherit; padding: 6px 12px; border-radius: 6px; border: 1px solid var(--line);
    background: transparent; color: inherit; cursor: pointer;
  }
  button.primary { background: var(--accent); border-color: var(--accent); color: var(--bg); }
  .locked {
    border: 1px dashed var(--line); border-radius: 8px; padding: 8px 12px; margin-top: 10px;
    color: var(--muted); font-size: 13px;
  }
  .reveal { margin-top: 10px; font-size: 13px; }
  pre.cmd {
    background: var(--bg); border: 1px solid var(--line); border-radius: 6px;
    padding: 10px; overflow-x: auto; font-size: 12px; margin: 10px 0 0;
  }
  .warn { color: var(--warn); }
</style>
</head>
<body>
<header>
  <h1>Alignment sync review</h1>
  <span class="muted mono" id="meta"></span>
  <span style="flex:1"></span>
  <label class="muted">Reviewer <input type="text" id="reviewer" placeholder="your name"></label>
  <label class="muted">Show
    <select id="filter">
      <option value="unreviewed">not yet judged</option>
      <option value="all">everything</option>
      <option value="reviewed">already judged</option>
    </select>
  </label>
  <span class="mono" id="progress"></span>
  <button class="primary" id="download">Download filled worksheet</button>
</header>
<main>
  <details class="guide" open>
    <summary>How to judge, and how to save</summary>
    __GUIDE__
    <h2>Saving your work</h2>
    <p>Answers save to this browser as you go. When you are done press
      <strong>Download filled worksheet</strong> and import it:</p>
    <pre class="cmd" id="import-cmd"></pre>
  </details>
  <div id="items"></div>
</main>
<script type="application/json" id="payload">__PAYLOAD__</script>
<script>
(function () {
  const data = JSON.parse(document.getElementById("payload").textContent);
  const items = data.worksheet.items;
  const runId = data.worksheet.run_id;
  const storeKey = "alignment-review:" + runId;
  const state = load();

  function load() {
    try {
      const raw = localStorage.getItem(storeKey);
      if (raw) return JSON.parse(raw);
    } catch (error) { /* private windows and blocked storage are fine */ }
    return { reviewer: "", answers: {} };
  }
  function save() {
    try { localStorage.setItem(storeKey, JSON.stringify(state)); } catch (error) {}
    renderProgress();
  }
  function answer(id) {
    if (!state.answers[id]) state.answers[id] = { ratings: {}, tags: {}, note: "" };
    return state.answers[id];
  }
  const labelsOf = (item) => Object.keys(item.assignment).sort();
  const isDone = (item) => {
    const record = answer(item.review_id);
    return labelsOf(item).every((label) => record.ratings[label]);
  };
  const playable = (item) => Boolean(data.audio[item.segment_id]);

  function renderProgress() {
    const pool = items.filter(playable);
    const done = pool.filter(isDone).length;
    document.getElementById("progress").textContent = done + " / " + pool.length + " judged";
    document.querySelectorAll(".card").forEach((card) => {
      const item = items.find((row) => row.review_id === card.dataset.id);
      card.classList.toggle("done", Boolean(item) && isDone(item));
    });
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"]/g, (character) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[character]);
  }

  // Render the sentence once per system, splitting on whitespace so every word is a span.
  // Words the system did not time are marked and never highlight, rather than being hidden:
  // an alignment with gaps should look like one.
  function karaoke(item, label) {
    const timed = (data.timing[item.review_id] || {})[label] || [];
    const box = document.createElement("p");
    box.className = "karaoke";
    const words = item.text.split(/(\s+)/);
    let cursor = 0;
    const spans = [];
    for (const chunk of words) {
      if (!chunk.trim()) { box.append(document.createTextNode(chunk)); continue; }
      const span = document.createElement("span");
      span.className = "w";
      span.textContent = chunk;
      const entry = timed[cursor];
      const bare = chunk.replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, "");
      if (entry && bare && entry.text && entry.text.replace(/[^\p{L}\p{N}]/gu, "").length) {
        span.dataset.start = entry.start;
        cursor += 1;
      } else if (bare) {
        span.classList.add("untimed");
      }
      spans.push(span);
      box.append(span);
    }
    return { box, spans };
  }

  function card(item, index) {
    const element = document.createElement("section");
    element.className = "card";
    element.dataset.id = item.review_id;

    const head = document.createElement("div");
    head.className = "row";
    head.innerHTML =
      "<strong>#" + (index + 1) + "</strong>" +
      '<span class="badge">' + item.language + "</span>" +
      '<span class="badge">' + item.source_class + "</span>" +
      '<span class="mono muted">' + item.duration.toFixed(1) + "s</span>";
    element.append(head);

    if (!playable(item)) {
      const warn = document.createElement("p");
      warn.className = "warn";
      warn.textContent = "No prepared clip for this row; leave it blank.";
      element.append(warn);
      return element;
    }

    const player = document.createElement("audio");
    player.controls = true;
    player.preload = "none";
    player.src = data.audio[item.segment_id];
    element.append(player);

    const groups = [];
    for (const label of labelsOf(item)) {
      const block = document.createElement("div");
      block.className = "system";
      const title = document.createElement("h4");
      title.textContent = label;
      const rendered = karaoke(item, label);
      groups.push(rendered.spans);

      const choices = document.createElement("div");
      choices.className = "choices";
      for (const rating of data.ratings) {
        const option = document.createElement("label");
        option.className = "opt";
        option.title = rating.anchor;
        const input = document.createElement("input");
        input.type = "radio";
        input.name = "rating:" + item.review_id + ":" + label;
        input.value = rating.id;
        input.checked = answer(item.review_id).ratings[label] === rating.id;
        input.addEventListener("change", () => {
          answer(item.review_id).ratings[label] = rating.id;
          save();
          gate();
        });
        option.append(input, document.createTextNode(rating.label));
        choices.append(option);
      }

      const tagBox = document.createElement("div");
      tagBox.className = "tags";
      for (const tag of data.tags) {
        const option = document.createElement("label");
        option.className = "opt";
        option.title = tag.anchor;
        const input = document.createElement("input");
        input.type = "checkbox";
        input.value = tag.id;
        const current = answer(item.review_id).tags[label] || [];
        input.checked = current.includes(tag.id);
        input.addEventListener("change", () => {
          const record = answer(item.review_id);
          const set = new Set(record.tags[label] || []);
          input.checked ? set.add(tag.id) : set.delete(tag.id);
          record.tags[label] = [...set];
          save();
        });
        option.append(input, document.createTextNode(tag.id));
        tagBox.append(option);
      }

      block.append(title, rendered.box, choices, tagBox);
      element.append(block);
    }

    // One timeupdate handler drives every rendering, so the three stay in lockstep.
    player.addEventListener("timeupdate", () => {
      const at = player.currentTime;
      for (const spans of groups) {
        let active = null;
        for (const span of spans) {
          if (span.dataset.start === undefined) continue;
          if (parseFloat(span.dataset.start) <= at) active = span;
        }
        for (const span of spans) span.classList.toggle("on", span === active);
      }
    });
    player.addEventListener("ended", () => {
      for (const spans of groups) for (const span of spans) span.classList.remove("on");
    });

    const note = document.createElement("fieldset");
    const legend = document.createElement("legend");
    legend.textContent = "Note (optional)";
    const area = document.createElement("textarea");
    area.placeholder = "Anything a later reader would need";
    area.value = answer(item.review_id).note || "";
    area.addEventListener("input", () => { answer(item.review_id).note = area.value; save(); });
    note.append(legend, area);
    element.append(note);

    const locked = document.createElement("div");
    locked.className = "locked";
    locked.textContent =
      "Which system is which stays hidden until you have rated all three, so the model " +
      "name cannot influence the rating.";
    const revealed = document.createElement("details");
    revealed.className = "reveal";
    revealed.innerHTML =
      "<summary>Reveal which system is which</summary>" +
      '<pre class="cmd">' + escapeHtml(JSON.stringify(item.assignment, null, 2)) + "</pre>";
    element.append(locked, revealed);

    function gate() {
      const unlocked = isDone(item);
      locked.hidden = unlocked;
      revealed.hidden = !unlocked;
    }
    gate();
    return element;
  }

  function visible() {
    const mode = document.getElementById("filter").value;
    return items.filter((item) => {
      if (mode === "all") return true;
      if (mode === "reviewed") return isDone(item);
      return !isDone(item);
    });
  }

  function draw() {
    const host = document.getElementById("items");
    host.textContent = "";
    visible().forEach((item, index) => host.append(card(item, index)));
    renderProgress();
  }

  document.getElementById("filter").addEventListener("change", draw);
  const reviewer = document.getElementById("reviewer");
  reviewer.value = state.reviewer || "";
  reviewer.addEventListener("input", () => { state.reviewer = reviewer.value; save(); });

  const playableCount = items.filter(playable).length;
  document.getElementById("meta").textContent =
    runId + " · rubric " + data.worksheet.rubric_version + " · " + playableCount +
    " clips (" + (items.length - playableCount) + " without audio) · " +
    Math.round(data.embeddedBytes / 1048576) + " MB embedded";

  document.getElementById("import-cmd").textContent =
    "uv run python experiments/forced-alignment/run_alignment.py review-import \\\n" +
    "    --run-id " + runId + " \\\n" +
    "    --worksheet ~/Downloads/alignment-review.filled.json";

  document.getElementById("download").addEventListener("click", () => {
    if (!state.reviewer.trim()) {
      alert("Enter a reviewer name first: the worksheet records who judged each row.");
      reviewer.focus();
      return;
    }
    const stamp = new Date().toISOString();
    const filled = JSON.parse(JSON.stringify(data.worksheet));
    for (const item of filled.items) {
      const record = state.answers[item.review_id];
      if (!record || !Object.keys(record.ratings).length) continue;
      item.ratings = record.ratings;
      item.tags = record.tags;
      item.note = record.note || null;
      item.reviewer = state.reviewer.trim();
      item.reviewed_at = stamp;
    }
    const blob = new Blob([JSON.stringify(filled, null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "alignment-review.filled.json";
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 2000);
  });

  document.addEventListener("keydown", (event) => {
    if (event.target.matches("input, textarea, select")) return;
    if (event.code !== "Space") return;
    const player = [...document.querySelectorAll("audio")].find((element) => {
      const box = element.getBoundingClientRect();
      return box.top > -40 && box.top < window.innerHeight * 0.6;
    });
    if (!player) return;
    event.preventDefault();
    player.currentTime = 0;
    player.play();
  });

  draw();
})();
</script>
</body>
</html>
"""
