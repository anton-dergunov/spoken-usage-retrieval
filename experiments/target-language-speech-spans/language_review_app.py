"""A self-contained, blind listening page for labelling the language of each speech unit.

The page carries only audio and unit timestamps. No transcript, detector probability, or method
decision is embedded, so nothing on the page can anchor the reviewer: a target-forced Whisper
transcript in particular reads as fluent target-language text even when the speech is English.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

MAX_EMBEDDED_BYTES = 64 * 1024 * 1024

LABEL_ANCHORS: tuple[tuple[str, str, str, str], ...] = (
    (
        "target",
        "1",
        "Target language",
        "Everything audible in the unit is the clip's target language. A name, a brand or one "
        "borrowed word inside it does not change that.",
    ),
    (
        "other",
        "2",
        "Other language",
        "Everything audible is some other language, usually the language the lesson is taught in.",
    ),
    (
        "mixed",
        "3",
        "Mixed",
        "Both the target language and another language are audible inside this unit, for "
        "example 'the word 认识 means to know'.",
    ),
    (
        "no_speech",
        "4",
        "No speech",
        "Only music, noise, laughter, breathing or a sound effect; no words at all.",
    ),
    (
        "unsure",
        "5",
        "Unsure",
        "You replayed it and still cannot tell which language it is, or it is too short to judge.",
    ),
)

GUIDE = """
<ol>
  <li>Each row is a short stretch of speech cut at natural pauses. Press <kbd>Space</kbd> to
  play it, <kbd>C</kbd> to play it with two seconds of context either side.</li>
  <li>Label only the <em>language you hear</em> in the highlighted unit. You do not need to
  understand it. Use the context playback to decide, but judge the unit itself.</li>
  <li>Keys <kbd>1</kbd>–<kbd>5</kbd> label and move to the next unit, which starts playing.
  <kbd>J</kbd>/<kbd>K</kbd> move without labelling. Answers save in this browser.</li>
  <li>Nothing on this page comes from the methods being evaluated. That is deliberate.</li>
</ol>
"""


def _script_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")


def collect_audio(worksheet: dict[str, Any]) -> tuple[dict[str, str], int]:
    audio: dict[str, str] = {}
    total = 0
    for clip in worksheet.get("clips", []):
        path = Path(clip["audio"])
        if not path.is_file():
            continue
        size = path.stat().st_size
        if total + size > MAX_EMBEDDED_BYTES:
            continue
        audio[clip["clip_id"]] = "data:audio/mp4;base64," + base64.b64encode(
            path.read_bytes()
        ).decode("ascii")
        total += size
    return audio, total


def render_language_review(
    worksheet: dict[str, Any], *, embed_audio: bool = True, server: bool = False
) -> str:
    """Render the reviewer. ``server=True`` streams audio from and saves every label to the
    local label server; otherwise audio is embedded and labels live in browser storage."""
    if server:
        audio = {
            clip["clip_id"]: f"audio/{clip['clip_id']}"
            for clip in worksheet["clips"]
            if Path(clip["audio"]).is_file()
        }
        embedded = 0
    else:
        audio, embedded = collect_audio(worksheet) if embed_audio else ({}, 0)
    public = {
        "run_id": worksheet["run_id"],
        "rubric_version": worksheet["rubric_version"],
        "items_checksum": worksheet["items_checksum"],
        "clips": [
            {k: clip[k] for k in ("clip_id", "target_language", "target_name", "duration")}
            for clip in worksheet["clips"]
        ],
        "items": [
            {k: item[k] for k in ("clip_id", "unit_id", "start", "end")}
            for item in worksheet["items"]
        ],
    }
    payload = {
        "worksheet": public,
        "audio": audio,
        "embeddedBytes": embedded,
        "server": server,
        "labels": [
            {"id": value, "key": key, "label": label, "anchor": anchor}
            for value, key, label, anchor in LABEL_ANCHORS
        ],
    }
    return _TEMPLATE.replace("__PAYLOAD__", _script_json(payload)).replace("__GUIDE__", GUIDE)


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Spoken language labels</title>
<style>
  :root {
    --bg: #f6f5f1; --card: #fff; --ink: #1d1c1a; --muted: #6d6a64; --line: #dcd8d0;
    --accent: #2f5d8a; --target: #2e7d4f; --other: #9a5b1e; --mixed: #7a4f9a;
    --none: #7b7b7b; --unsure: #b08a1e; --on-ink: #fff; color-scheme: light dark;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #161614; --card: #1f1e1b; --ink: #ecebe6; --muted: #a29e96; --line: #36342f;
      --accent: #8db8e0; --target: #7cc79a; --other: #e0a36a; --mixed: #c29ae0;
      --none: #aaa; --unsure: #e0c060; --on-ink: #141412;
    }
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; }
  body { margin: 0; background: var(--bg); color: var(--ink);
         font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         display: flex; flex-direction: column; height: 100vh; height: 100dvh; overflow: hidden; }
  header { flex: none; background: var(--card);
           border-bottom: 1px solid var(--line); padding: 10px 16px;
           display: flex; flex-wrap: wrap; gap: 12px; align-items: center; }
  h1 { font-size: 16px; margin: 0; }
  .muted { color: var(--muted); }
  .mono { font-family: ui-monospace, Menlo, monospace; font-size: 12px; }
  main { flex: 1 1 auto; min-height: 0; overflow-y: auto; overscroll-behavior: contain;
         -webkit-overflow-scrolling: touch; padding: 16px; }
  main > * { max-width: 920px; margin-left: auto; margin-right: auto; }
  details { background: var(--card); border: 1px solid var(--line); border-radius: 10px;
            padding: 10px 14px; margin-bottom: 14px; }
  summary { cursor: pointer; font-weight: 600; }
  kbd { border: 1px solid var(--line); border-bottom-width: 2px; border-radius: 4px;
        padding: 0 5px; font-size: 12px; }
  .legend { display: grid; gap: 6px; margin-top: 8px; }
  .legend div { display: flex; gap: 10px; }
  .legend b { min-width: 9.5em; }
  .clip { background: var(--card); border: 1px solid var(--line); border-radius: 10px;
          margin-bottom: 18px; overflow: hidden; }
  .clip h2 { font-size: 15px; margin: 0; padding: 10px 14px; border-bottom: 1px solid var(--line);
             display: flex; gap: 10px; flex-wrap: wrap; align-items: baseline; }
  .strip { display: flex; height: 16px; margin: 8px 14px; border-radius: 4px; overflow: hidden;
           background: color-mix(in srgb, var(--line) 50%, transparent); position: relative; }
  .strip span { position: absolute; top: 0; bottom: 0; }
  .rows { }
  .row { display: grid; grid-template-columns: 5.5em 7.5em 1fr; gap: 10px; padding: 6px 14px;
         border-top: 1px solid var(--line); align-items: center; cursor: pointer; }
  .row.current { background: color-mix(in srgb, var(--accent) 14%, transparent); }
  .choices { display: flex; flex-wrap: wrap; gap: 4px; }
  .choices button { font: inherit; font-size: 12px; padding: 2px 8px; border-radius: 999px;
                    border: 1px solid var(--line); background: transparent; color: inherit;
                    cursor: pointer; }
  [data-label=target] { --c: var(--target); } [data-label=other] { --c: var(--other); }
  [data-label=mixed] { --c: var(--mixed); } [data-label=no_speech] { --c: var(--none); }
  [data-label=unsure] { --c: var(--unsure); }
  .choices button[data-label], #pad-labels button {
    color: var(--c); border: 1.5px solid var(--c); background: transparent; font-weight: 500; }
  .choices button.on, #pad-labels button.on {
    background: var(--c); color: var(--on-ink); font-weight: 700; }
  .choices button.on::before, #pad-labels button.on::before { content: "✓ "; }
  .t { background: var(--target); } .o { background: var(--other); }
  .m { background: var(--mixed); } .n { background: var(--none); } .u { background: var(--unsure); }
  input[type=text] { font: inherit; padding: 4px 8px; border-radius: 6px;
                     border: 1px solid var(--line); background: transparent; color: inherit; }
  button.primary { font: inherit; padding: 5px 12px; border-radius: 6px; border: 0;
                   background: var(--accent); color: var(--bg); cursor: pointer; }
  #pad { flex: none; background: var(--card); border-top: 1px solid var(--line);
         padding: 8px 10px calc(8px + env(safe-area-inset-bottom)); display: grid; gap: 6px; }
  #pad-controls { display: grid; grid-template-columns: 1fr 1.4fr 1.4fr 1fr; gap: 6px; }
  #pad-labels { display: grid; grid-template-columns: repeat(5, 1fr); gap: 6px; }
  #pad button { font: inherit; font-size: 14px; padding: 12px 2px; border-radius: 10px;
                touch-action: manipulation; -webkit-user-select: none; user-select: none;
                min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  #pad-controls button { border: 1px solid var(--line); background: transparent; color: inherit; }
  #pad-current { font-size: 12px; text-align: center; }
  @media (hover: hover) and (pointer: fine) and (min-width: 900px) { #pad { display: none; } }
  @media (max-width: 560px) {
    #meta, header h1, #download { display: none; }
    header { padding: 8px 10px; gap: 8px; }
    main { padding: 10px; }
    #pad button { font-size: 13px; padding: 12px 1px; }
    .row { grid-template-columns: 1fr 1fr; }
    .choices { grid-column: 1 / -1; }
  }
</style>
</head>
<body>
<header>
  <h1>Spoken language labels</h1>
  <span class="mono muted" id="meta"></span>
  <span style="flex:1"></span>
  <label class="muted">Reviewer <input type="text" id="reviewer" placeholder="your name"></label>
  <span class="mono" id="progress"></span>
  <span class="mono" id="saved"></span>
  <button class="primary" id="download">Download labels</button>
</header>
<main>
  <details open>
    <summary>How to label</summary>
    __GUIDE__
    <div class="legend" id="legend"></div>
    <p class="muted">Saving:</p>
    <pre class="mono" id="import-cmd"></pre>
  </details>
  <div id="clips"></div>
</main>
<div id="pad">
  <div id="pad-current" class="mono muted"></div>
  <div id="pad-controls">
    <button id="pad-prev" aria-label="Previous unit">◀</button>
    <button id="pad-play">▶ Play</button>
    <button id="pad-context">⟲ Context</button>
    <button id="pad-next" aria-label="Next unit">▶▶</button>
  </div>
  <div id="pad-labels"></div>
</div>
<script type="application/json" id="payload">__PAYLOAD__</script>
<script>
(function () {
  const data = JSON.parse(document.getElementById("payload").textContent);
  const sheet = data.worksheet;
  const items = sheet.items;
  const storeKey = "language-labels:" + sheet.run_id + ":" + sheet.items_checksum;
  const classes = { target: "t", other: "o", mixed: "m", no_speech: "n", unsure: "u" };
  let state = loadLocal();
  const players = {};
  let current = 0;
  let stopAt = null;
  const savedNode = document.getElementById("saved");

  function loadLocal() {
    try { const raw = localStorage.getItem(storeKey); if (raw) return JSON.parse(raw); }
    catch (error) {}
    return { reviewer: "", labels: {}, times: {} };
  }
  function save() {
    try { localStorage.setItem(storeKey, JSON.stringify(state)); } catch (error) {}
    progress();
  }
  function status(text, bad) {
    savedNode.textContent = text;
    savedNode.style.color = bad ? "var(--other)" : "var(--target)";
  }
  async function post(path, body) {
    const response = await fetch(path, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || response.statusText);
    return payload;
  }
  async function persist(unitId, label) {
    if (!data.server) return;
    status("saving…", false);
    try {
      const result = await post("api/label", { unit_id: unitId, label: label });
      status("saved to file · " + result.labelled + " labelled", false);
    } catch (error) {
      status("NOT SAVED: " + error.message, true);
      if (/reviewer/i.test(error.message)) {
        delete state.labels[unitId];
        save();
        items.forEach((_, index) => paintRow(index));
        alert("Enter a reviewer name first.");
        document.getElementById("reviewer").focus();
      }
    }
  }
  async function loadServer() {
    const response = await fetch("api/state");
    const remote = await response.json();
    const pending = Object.keys(state.labels).filter((id) => !remote.labels[id]);
    state = { reviewer: remote.reviewer || state.reviewer || "", labels: remote.labels,
              times: remote.times };
    if (state.reviewer && !remote.reviewer) await post("api/reviewer", { reviewer: state.reviewer });
    // Labels made earlier in the offline page are uploaded once, never overwriting the file.
    for (const id of pending) {
      const local = loadLocal();
      if (!state.reviewer) break;
      state.labels[id] = local.labels[id];
      await persist(id, local.labels[id]);
    }
    save();
    status("saved to file · " + Object.keys(state.labels).length + " labelled", false);
  }
  function firstUnlabelled() {
    const index = items.findIndex((item) => !state.labels[item.unit_id]);
    return index < 0 ? 0 : index;
  }
  function fmt(seconds) {
    const m = Math.floor(seconds / 60), s = seconds - m * 60;
    return m + ":" + s.toFixed(1).padStart(4, "0");
  }
  function progress() {
    const done = items.filter((item) => state.labels[item.unit_id]).length;
    document.getElementById("progress").textContent = done + " / " + items.length + " labelled";
  }

  function player(clipId) {
    if (!players[clipId] && data.audio[clipId]) {
      const element = new Audio(data.audio[clipId]);
      element.preload = "auto";
      element.addEventListener("timeupdate", () => {
        if (stopAt !== null && element.currentTime >= stopAt) { element.pause(); stopAt = null; }
      });
      players[clipId] = element;
    }
    return players[clipId];
  }
  function play(index, context) {
    const item = items[index];
    Object.values(players).forEach((p) => p.pause());
    const element = player(item.clip_id);
    if (!element) return;
    const pad = context ? 2.0 : 0.0;
    const from = Math.max(0, item.start - pad);
    stopAt = item.end + pad;
    // Mobile Safari ignores a seek made before metadata has loaded, so reapply it once it has.
    if (element.readyState >= 1) {
      element.currentTime = from;
    } else {
      element.addEventListener("loadedmetadata", () => { element.currentTime = from; },
        { once: true });
    }
    element.play().catch(() => {});
  }

  function setLabel(index, label) {
    const item = items[index];
    state.labels[item.unit_id] = label;
    state.times[item.unit_id] = new Date().toISOString();
    save();
    paintRow(index);
    persist(item.unit_id, label);
  }
  function paintRow(index) {
    const item = items[index];
    const row = document.getElementById("row-" + index);
    if (!row) return;
    row.classList.toggle("current", index === current);
    for (const button of row.querySelectorAll("button")) {
      const on = state.labels[item.unit_id] === button.dataset.label;
      button.classList.toggle("on", on);
    }
    const mark = document.getElementById("mark-" + index);
    if (mark) mark.className = classes[state.labels[item.unit_id]] || "";
    if (index === current) paintPad();
  }
  function paintPad() {
    const item = items[current];
    if (!item) return;
    const label = state.labels[item.unit_id];
    const option = data.labels.find((entry) => entry.id === label);
    document.getElementById("pad-current").textContent = "#" + (current + 1) + " of " +
      items.length + " · " + item.clip_id + " · " + fmt(item.start) + " · " +
      (option ? option.label : "not labelled yet");
    for (const button of document.querySelectorAll("#pad-labels button")) {
      button.classList.toggle("on", button.dataset.label === label);
    }
  }
  function move(index, autoplay) {
    const previous = current;
    current = Math.max(0, Math.min(items.length - 1, index));
    paintRow(previous);
    paintRow(current);
    const row = document.getElementById("row-" + current);
    if (row) row.scrollIntoView({ block: "center", behavior: "smooth" });
    paintPad();
    if (autoplay) play(current, false);
  }

  function draw() {
    const host = document.getElementById("clips");
    for (const clip of sheet.clips) {
      const section = document.createElement("section");
      section.className = "clip";
      const units = items.map((item, index) => [item, index]).filter(([item]) => item.clip_id === clip.clip_id);
      section.innerHTML = "<h2><span>Target language: " + clip.target_name + " (" +
        clip.target_language + ")</span><span class=\"mono muted\">" + clip.clip_id + " · " +
        units.length + " units · " + fmt(clip.duration) + "</span>" +
        (data.audio[clip.clip_id] ? "" : "<span style=\"color:var(--other)\">audio missing</span>") +
        "</h2>";
      const strip = document.createElement("div");
      strip.className = "strip";
      for (const [item, index] of units) {
        const mark = document.createElement("span");
        mark.id = "mark-" + index;
        mark.style.left = (100 * item.start / clip.duration) + "%";
        mark.style.width = Math.max(0.15, 100 * (item.end - item.start) / clip.duration) + "%";
        strip.append(mark);
      }
      section.append(strip);
      const rows = document.createElement("div");
      rows.className = "rows";
      for (const [item, index] of units) {
        const row = document.createElement("div");
        row.className = "row";
        row.id = "row-" + index;
        row.innerHTML = "<span class=\"mono\">#" + (index + 1) + "</span><span class=\"mono muted\">" +
          fmt(item.start) + " · " + (item.end - item.start).toFixed(1) + "s</span>";
        const choices = document.createElement("div");
        choices.className = "choices";
        for (const option of data.labels) {
          const button = document.createElement("button");
          button.dataset.label = option.id;
          button.title = option.anchor;
          button.textContent = option.key + " " + option.label;
          button.addEventListener("click", (event) => {
            event.stopPropagation();
            current = index;
            setLabel(index, option.id);
            move(index + 1, true);
          });
          choices.append(button);
        }
        row.append(choices);
        row.addEventListener("click", () => { move(index, true); });
        rows.append(row);
      }
      section.append(rows);
      host.append(section);
    }
    items.forEach((_, index) => paintRow(index));
  }

  document.addEventListener("keydown", (event) => {
    if (event.target.matches("input, textarea")) return;
    const option = data.labels.find((entry) => entry.key === event.key);
    if (option) {
      event.preventDefault();
      setLabel(current, option.id);
      move(current + 1, true);
      return;
    }
    const key = event.key.toLowerCase();
    if (event.code === "Space") { event.preventDefault(); play(current, false); }
    else if (key === "c") { play(current, true); }
    else if (key === "j") { move(current + 1, true); }
    else if (key === "k") { move(current - 1, true); }
  });

  const legend = document.getElementById("legend");
  for (const option of data.labels) {
    const line = document.createElement("div");
    line.innerHTML = "<b><kbd>" + option.key + "</kbd> " + option.label + "</b><span class=\"muted\">" +
      option.anchor + "</span>";
    legend.append(line);
  }
  document.getElementById("meta").textContent = sheet.run_id + " · rubric " +
    sheet.rubric_version + " · " + (data.server ? "saved to the label file"
      : Math.round(data.embeddedBytes / 1048576) + " MB audio embedded");
  document.getElementById("import-cmd").textContent = data.server
    ? "Every label is written to the label file as you press the key; nothing to download."
    : "uv run python experiments/target-language-speech-spans/run_speech_spans.py review-import \\\n" +
      "    --worksheet ~/Downloads/language-labels.filled.json";
  const reviewer = document.getElementById("reviewer");
  reviewer.addEventListener("change", () => {
    state.reviewer = reviewer.value;
    save();
    if (data.server) post("api/reviewer", { reviewer: reviewer.value.trim() })
      .then(() => status("reviewer saved", false))
      .catch((error) => status("NOT SAVED: " + error.message, true));
  });

  document.getElementById("download").addEventListener("click", () => {
    if (!state.reviewer.trim()) {
      alert("Enter a reviewer name first.");
      reviewer.focus();
      return;
    }
    const filled = {
      run_id: sheet.run_id,
      rubric_version: sheet.rubric_version,
      items_checksum: sheet.items_checksum,
      items: items.map((item) => ({
        clip_id: item.clip_id, unit_id: item.unit_id, start: item.start, end: item.end,
        label: state.labels[item.unit_id] || null, note: null,
        reviewer: state.labels[item.unit_id] ? state.reviewer.trim() : null,
        reviewed_at: state.times[item.unit_id] || null,
      })),
    };
    const blob = new Blob([JSON.stringify(filled, null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "language-labels.filled.json";
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 2000);
  });

  const padLabels = document.getElementById("pad-labels");
  const shortNames = { target: "Target", other: "Other", mixed: "Mixed", no_speech: "No speech",
                       unsure: "Unsure" };
  document.getElementById("pad-play").addEventListener("click", () => play(current, false));
  document.getElementById("pad-context").addEventListener("click", () => play(current, true));
  document.getElementById("pad-prev").addEventListener("click", () => move(current - 1, true));
  document.getElementById("pad-next").addEventListener("click", () => move(current + 1, true));
  for (const option of data.labels) {
    const button = document.createElement("button");
    button.dataset.label = option.id;
    button.title = option.anchor;
    button.textContent = shortNames[option.id] || option.label;
    button.addEventListener("click", () => {
      setLabel(current, option.id);
      move(current + 1, true);
    });
    padLabels.append(button);
  }

  async function start() {
    if (data.server) {
      try { await loadServer(); }
      catch (error) { status("label server unreachable: " + error.message, true); }
    }
    reviewer.value = state.reviewer || "";
    current = firstUnlabelled();
    draw();
    progress();
    move(current, false);
  }
  start();
})();
</script>
</body>
</html>
"""
