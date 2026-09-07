"""A self-contained local HTML worksheet for the manual review pass.

The page embeds the exported worksheet and each prepared clip, so it opens straight from
disk with no server and no network. Judgements are kept in the browser's local storage and
exported as a filled worksheet that ``review-import`` reads back.

The automatic disagreement rate is deliberately hidden behind a per-item toggle: a reviewer
who sees the metric first is anchored by it, and the whole point of this pass is to decide
independently whether a disagreement is a real caption error.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from caption_reliability import ACOUSTIC_VOCABULARY, REVIEW_TAGS

MAX_EMBEDDED_BYTES = 64 * 1024 * 1024

CAPTION_VERDICTS: tuple[tuple[str, str, str], ...] = (
    (
        "correct",
        "Correct",
        "Every spoken word appears in the caption and every caption word is spoken. Only "
        "punctuation, casing, or spacing differ.",
    ),
    (
        "acceptable",
        "Acceptable",
        "Small differences that do not change the meaning: a dropped filler, a cleaned-up "
        "false start, a contraction, digits instead of a written-out number. A learner "
        "reading this caption would not be misled.",
    ),
    (
        "incorrect",
        "Incorrect",
        "At least one difference that misleads: a content word wrong, missing, or invented, "
        "a changed meaning, or a wrong name, place, or number.",
    ),
    (
        "uncertain",
        "Uncertain",
        "You genuinely cannot decide after replaying: the speech is unclear, speakers "
        "overlap, or you do not know the word or the regional usage.",
    ),
)

REFERENCE_ASSESSMENTS: tuple[tuple[str, str, str], ...] = (
    (
        "equivalent",
        "Equivalent",
        "Caption and ASR transcribe the audio equally well. Any difference between them is "
        "punctuation, casing, or formatting.",
    ),
    (
        "caption_better",
        "Caption better",
        "The caption is closer to what is actually said. The ASR made the mistake.",
    ),
    (
        "asr_better",
        "ASR better",
        "The ASR is closer to what is actually said. The caption made the mistake.",
    ),
    (
        "both_wrong",
        "Both wrong",
        "Neither matches the audio. Use the corrected transcript to say what is said.",
    ),
    (
        "uncertain",
        "Uncertain",
        "You cannot tell which is closer to the audio.",
    ),
)

TAG_ANCHORS: dict[str, str] = {
    "meaning_change": ("The difference changes what is being said, not just how it is written."),
    "omitted_speech": "Words are clearly audible but absent from the caption.",
    "extra_speech": "The caption contains words that are not spoken in the clip.",
    "name_or_entity_error": ("A person, place, brand, or number is transcribed as something else."),
    "disfluency_difference": (
        "The only difference is fillers, repetitions, or false starts ('eh', 'o sea', a "
        "restarted word)."
    ),
    "punctuation_only_difference": (
        "The only difference is punctuation, casing, or spacing. Do not use this together "
        "with meaning_change."
    ),
    "boundary_problem": (
        "The caption text itself is not a coherent unit: it starts or ends mid-utterance, or "
        "it merges two speakers' turns."
    ),
    "start_cut": "The audio clip begins after the utterance has already started.",
    "end_cut": "The audio clip ends before the utterance finishes.",
    "overlap": "Two or more people speak at the same time inside the clip.",
    "noise_or_music": "Background music or noise competes with the speech.",
    "unclear_speech": (
        "The speech is hard to make out even after replaying, independently of noise."
    ),
}

ACOUSTIC_ANCHORS: dict[str, str] = {
    "clean_single_speaker": (
        "One speaker, close microphone, no competing sound. The default when nothing else applies."
    ),
    "background_music_or_noise": (
        "Music, traffic, a crowd, or room noise is audible under the speech."
    ),
    "overlapping_speakers": "More than one voice is audible at the same time.",
    "distant_or_reverberant": (
        "The speaker sounds far from the microphone, echoey, or in a large room."
    ),
    "fast_speech": (
        "Noticeably faster than this speaker's or this channel's normal pace; words run together."
    ),
}

GUIDE = """
<h2>How to review one clip</h2>
<ol>
  <li><strong>Listen first.</strong> Play the clip before you read anything. Replay it as
      often as you need.</li>
  <li><strong>Judge the caption against the audio</strong>, never against the ASR text. The
      ASR is a declared comparison reference, not truth: it can be wrong in the same place
      the caption is, or wrong where the caption is right.</li>
  <li><strong>Then judge the ASR</strong> in the second question. This is what separates a
      caption error from a reference error.</li>
  <li><strong>Tag only what applies.</strong> Empty tag lists are fine and common.</li>
  <li><strong>Assign acoustic tags from listening only.</strong> Never from the numbers: the
      voice-activity and quality features are being evaluated <em>against</em> these tags, so
      reading them first would make the evaluation circular.</li>
  <li>The automatic disagreement rate is hidden. Reveal it only after you have recorded your
      verdict, and only if you are curious.</li>
</ol>
<h2>Why these rows?</h2>
<p id="subset-note"></p>
<p>The review subset is predeclared in the configuration, before any result is looked at, so the
choice of what to review cannot be steered by the outcome. It is every row that failed a pipeline
stage, plus the first few rows per disagreement bin per source class in stable hash order. A bin
contributes fewer than its quota when it does not contain that many rows.</p>
<p><strong>Rows without audio are not review items.</strong> They are pipeline gaps recorded
against the same frozen row: the row keeps its identity instead of being silently replaced by a
different segment. Leave them blank.</p>
"""


def _audio_data_uri(path: Path) -> str | None:
    try:
        payload = path.read_bytes()
    except OSError:
        return None
    return "data:audio/wav;base64," + base64.b64encode(payload).decode("ascii")


def collect_audio(worksheet: dict[str, Any]) -> tuple[dict[str, str], int]:
    """Return per-segment data URIs and the total embedded byte count."""
    audio: dict[str, str] = {}
    total = 0
    for item in worksheet.get("items", []):
        raw = item.get("clip")
        if not raw:
            continue
        path = Path(raw)
        if not path.is_file():
            continue
        size = path.stat().st_size
        if total + size > MAX_EMBEDDED_BYTES:
            continue
        encoded = _audio_data_uri(path)
        if encoded is None:
            continue
        audio[item["segment_id"]] = encoded
        total += size
    return audio, total


def _script_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")


def render_review_app(worksheet: dict[str, Any], *, embed_audio: bool = True) -> str:
    """Render the whole reviewer as one standalone HTML document."""
    audio: dict[str, str] = {}
    embedded_bytes = 0
    if embed_audio:
        audio, embedded_bytes = collect_audio(worksheet)
    tags = [
        {"id": tag, "anchor": TAG_ANCHORS[tag]}
        for tag in worksheet.get("review_tags", REVIEW_TAGS)
        if tag in TAG_ANCHORS
    ]
    acoustics = [
        {"id": tag, "anchor": ACOUSTIC_ANCHORS[tag]}
        for tag in worksheet.get("acoustic_vocabulary", ACOUSTIC_VOCABULARY)
        if tag in ACOUSTIC_ANCHORS
    ]
    payload = {
        "worksheet": worksheet,
        "audio": audio,
        "embeddedBytes": embedded_bytes,
        "verdicts": [
            {"id": value, "label": label, "anchor": anchor}
            for value, label, anchor in CAPTION_VERDICTS
        ],
        "assessments": [
            {"id": value, "label": label, "anchor": anchor}
            for value, label, anchor in REFERENCE_ASSESSMENTS
        ],
        "tags": tags,
        "acoustics": acoustics,
    }
    return _TEMPLATE.replace("__PAYLOAD__", _script_json(payload)).replace("__GUIDE__", GUIDE)


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Caption review worksheet</title>
<style>
  :root {
    --bg: #f7f6f3; --card: #ffffff; --ink: #1c1b19; --muted: #6b6864;
    --line: #dedad3; --accent: #7a5c2e; --warn: #8a4b2a; --ok: #3f6b45;
    color-scheme: light dark;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #171614; --card: #201f1c; --ink: #ece9e3; --muted: #a09b93;
      --line: #34322d; --accent: #d4b483; --warn: #e0a080; --ok: #8fc396;
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
  main { max-width: 900px; margin: 0 auto; padding: 20px; }
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
  .card.noaudio { opacity: .72; }
  .row { display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; }
  .badge {
    font-size: 11px; letter-spacing: .02em; text-transform: uppercase;
    border: 1px solid var(--line); border-radius: 999px; padding: 2px 8px; color: var(--muted);
  }
  audio { width: 100%; margin: 12px 0 4px; }
  .texts { display: grid; gap: 10px; margin: 12px 0; }
  @media (min-width: 720px) { .texts { grid-template-columns: 1fr 1fr; } }
  .text { border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; }
  .text h4 { margin: 0 0 6px; font-size: 11px; text-transform: uppercase;
             letter-spacing: .04em; color: var(--muted); font-weight: 600; }
  .text p { margin: 0; font-size: 16px; }
  fieldset { border: 0; padding: 0; margin: 14px 0 0; }
  legend { font-size: 12px; font-weight: 650; padding: 0; margin-bottom: 6px; }
  .opts { display: grid; gap: 4px; }
  label.opt {
    display: flex; gap: 8px; align-items: flex-start; padding: 6px 8px;
    border-radius: 6px; cursor: pointer;
  }
  label.opt:hover { background: color-mix(in srgb, var(--accent) 8%, transparent); }
  label.opt input { margin-top: 4px; flex: none; }
  label.opt b { font-weight: 600; }
  label.opt span { color: var(--muted); font-size: 13px; }
  textarea {
    width: 100%; min-height: 46px; margin-top: 6px; padding: 8px; border-radius: 6px;
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
  .reveal { margin-top: 10px; font-size: 13px; }
  .reveal summary { cursor: pointer; color: var(--muted); }
  pre.cmd {
    background: var(--bg); border: 1px solid var(--line); border-radius: 6px;
    padding: 10px; overflow-x: auto; font-size: 12px; margin: 10px 0 0;
  }
  .warn { color: var(--warn); }
</style>
</head>
<body>
<header>
  <h1>Caption review</h1>
  <span class="muted mono" id="meta"></span>
  <span style="flex:1"></span>
  <label class="muted">Reviewer <input type="text" id="reviewer" placeholder="your name"></label>
  <label class="muted">Show
    <select id="filter">
      <option value="reviewable">to judge</option>
      <option value="unreviewed">not yet judged</option>
      <option value="reviewed">already judged</option>
      <option value="noaudio">no audio, not reviewable</option>
      <option value="all">everything in the subset</option>
    </select>
  </label>
  <span class="mono" id="progress"></span>
  <button class="primary" id="download">Download filled worksheet</button>
</header>
<main>
  <details class="guide" open>
    <summary>Rubric and how to judge</summary>
    __GUIDE__
    <h2>Saving your work</h2>
    <p>Answers save to this browser automatically as you type. When you are done, press
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
  const storeKey = "caption-review:" + runId;
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
    if (!state.answers[id]) {
      state.answers[id] = {
        caption_verdict: null, reference_assessment: null,
        tags: [], acoustic_tags: [], note: "", corrected_transcript: "",
      };
    }
    return state.answers[id];
  }
  const reviewable = (item) => Boolean(data.audio[item.segment_id]);
  const isDone = (item) => Boolean(answer(item.segment_id).caption_verdict);

  function renderProgress() {
    const pool = items.filter(reviewable);
    const done = pool.filter(isDone).length;
    document.getElementById("progress").textContent = done + " / " + pool.length + " judged";
    document.querySelectorAll(".card").forEach((card) => {
      const item = items.find((row) => row.segment_id === card.dataset.id);
      card.classList.toggle("done", Boolean(item) && isDone(item));
    });
  }

  function options(item, group, list, multiple) {
    const wrap = document.createElement("div");
    wrap.className = "opts";
    for (const option of list) {
      const label = document.createElement("label");
      label.className = "opt";
      const input = document.createElement("input");
      input.type = multiple ? "checkbox" : "radio";
      input.name = group + ":" + item.segment_id;
      input.value = option.id;
      const current = answer(item.segment_id);
      input.checked = multiple
        ? current[group].includes(option.id)
        : current[group] === option.id;
      input.disabled = !reviewable(item);
      input.addEventListener("change", () => {
        const record = answer(item.segment_id);
        if (multiple) {
          const set = new Set(record[group]);
          input.checked ? set.add(option.id) : set.delete(option.id);
          record[group] = [...set];
        } else {
          record[group] = input.value;
        }
        save();
      });
      const text = document.createElement("div");
      text.innerHTML = "<b>" + (option.label || option.id) + "</b> <span>" +
        option.anchor + "</span>";
      label.append(input, text);
      wrap.append(label);
    }
    return wrap;
  }

  function field(item, key, legendText, placeholder) {
    const set = document.createElement("fieldset");
    const legend = document.createElement("legend");
    legend.textContent = legendText;
    const area = document.createElement("textarea");
    area.placeholder = placeholder;
    area.value = answer(item.segment_id)[key] || "";
    area.disabled = !reviewable(item);
    area.addEventListener("input", () => {
      answer(item.segment_id)[key] = area.value;
      save();
    });
    set.append(legend, area);
    return set;
  }

  function card(item, index) {
    const element = document.createElement("section");
    element.className = "card" + (reviewable(item) ? "" : " noaudio");
    element.dataset.id = item.segment_id;

    const head = document.createElement("div");
    head.className = "row";
    head.innerHTML =
      "<strong>#" + (index + 1) + "</strong>" +
      '<span class="badge">' + item.stratum + "</span>" +
      '<span class="badge">' + (item.channel || "unknown channel") + "</span>" +
      '<span class="mono muted">' + item.segment_id + "</span>";
    element.append(head);

    if (reviewable(item)) {
      const player = document.createElement("audio");
      player.controls = true;
      player.preload = "none";
      player.src = data.audio[item.segment_id];
      element.append(player);
    } else {
      const warn = document.createElement("p");
      warn.className = "warn";
      warn.textContent =
        "No prepared clip: this row failed earlier in the pipeline and is not a review " +
        "item. Leave it blank.";
      element.append(warn);
    }

    const texts = document.createElement("div");
    texts.className = "texts";
    texts.innerHTML =
      '<div class="text"><h4>Caption (the thing being judged)</h4><p>' +
      escapeHtml(item.caption_text || "") + "</p></div>" +
      '<div class="text"><h4>ASR reference (not truth)</h4><p>' +
      escapeHtml(item.asr_text || "—") + "</p></div>";
    element.append(texts);

    element.append(
      wrapField("1. Is the caption an accurate transcript of this audio?",
        options(item, "caption_verdict", data.verdicts, false)),
      wrapField("2. How does the ASR reference compare with the caption?",
        options(item, "reference_assessment", data.assessments, false)),
      wrapField("3. What kinds of difference or problem apply? (none is fine)",
        options(item, "tags", data.tags, true)),
      wrapField("4. What does the audio sound like? (from listening only)",
        options(item, "acoustic_tags", data.acoustics, true)),
      field(item, "corrected_transcript", "5. Corrected transcript (only when it explains the discrepancy)",
        "What is actually said"),
      field(item, "note", "6. Note (optional)", "Anything a later reader would need"),
    );

    const reveal = document.createElement("details");
    reveal.className = "reveal";
    reveal.innerHTML =
      "<summary>Reveal the automatic disagreement rate (after you decide)</summary>" +
      '<pre class="cmd">' + escapeHtml(JSON.stringify({
        error_rate: item.error_rate,
        effective_start: item.effective_start ?? null,
        effective_end: item.effective_end ?? null,
        video_key: item.video_key,
        clip: item.clip,
      }, null, 2)) + "</pre>";
    element.append(reveal);
    return element;
  }

  function wrapField(legendText, body) {
    const set = document.createElement("fieldset");
    const legend = document.createElement("legend");
    legend.textContent = legendText;
    set.append(legend, body);
    return set;
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"]/g, (character) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[character]);
  }

  function visible() {
    const mode = document.getElementById("filter").value;
    return items.filter((item) => {
      if (mode === "all") return true;
      if (mode === "noaudio") return !reviewable(item);
      if (mode === "reviewable") return reviewable(item);
      if (mode === "unreviewed") return reviewable(item) && !isDone(item);
      return reviewable(item) && isDone(item);
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

  const judgeable = items.filter(reviewable).length;
  const withoutAudio = items.length - judgeable;
  document.getElementById("meta").textContent =
    runId + " · rubric " + data.worksheet.rubric_version + " · " +
    judgeable + " to judge, " + withoutAudio + " without audio (" + items.length +
    " of " + data.worksheet.total + " sampled rows in the review subset) · " +
    Math.round(data.embeddedBytes / 1048576) + " MB audio embedded";

  document.getElementById("subset-note").textContent =
    "This run froze " + data.worksheet.total + " sampled segments. " + items.length +
    " of them are in the predeclared review subset: " + judgeable +
    " have a prepared clip and are yours to judge, and " + withoutAudio +
    " failed earlier in the pipeline and have no audio to listen to.";

  for (const option of document.querySelectorAll("#filter option")) {
    const counts = {
      reviewable: judgeable, unreviewed: judgeable, reviewed: judgeable,
      noaudio: withoutAudio, all: items.length,
    };
    option.textContent = option.textContent + " (" + counts[option.value] + ")";
  }

  document.getElementById("import-cmd").textContent =
    "uv run python experiments/audio-caption-reliability/run_reliability.py review-import \\\n" +
    "    --run-id " + runId + " \\\n" +
    "    --worksheet ~/Downloads/review-worksheet.filled.json";

  document.getElementById("download").addEventListener("click", () => {
    if (!state.reviewer.trim()) {
      alert("Enter a reviewer name first: the rubric records who judged each row.");
      reviewer.focus();
      return;
    }
    const stamp = new Date().toISOString();
    const filled = JSON.parse(JSON.stringify(data.worksheet));
    for (const item of filled.items) {
      const record = state.answers[item.segment_id];
      if (!record || !record.caption_verdict) continue;
      item.caption_verdict = record.caption_verdict;
      item.reference_assessment = record.reference_assessment;
      item.tags = record.tags;
      item.acoustic_tags = record.acoustic_tags;
      item.note = record.note || null;
      item.corrected_transcript = record.corrected_transcript || null;
      item.reviewer = state.reviewer.trim();
      item.reviewed_at = stamp;
    }
    const blob = new Blob([JSON.stringify(filled, null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "review-worksheet.filled.json";
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
