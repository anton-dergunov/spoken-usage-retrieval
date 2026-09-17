# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "mlx-audio==0.5.4",
#   "numpy>=2,<3",
#   "pydantic>=2.11,<3",
#   "soundfile>=0.13,<1",
# ]
# ///
"""Render synthetic code-switched lessons with exact language ground truth.

Runs outside the project environment (``uv run --script``) so the macOS-only TTS stack never
enters the lockfile. Two voice sources:

- ``system:<Family>`` uses macOS ``say`` voice families that speak every supported language with
  one timbre, so a language switch is never also a speaker switch. Russian and Hindi have no
  family voice; they fall back to a single-language voice and the clip records the confound.
- ``chatterbox:<reference>`` clones one synthetic reference voice across both languages with
  Chatterbox multilingual (mlx).
- ``system-accented:<Family>`` reads target-language text with the family's English voice, a
  crude stand-in for a non-native speaker. Latin-script targets only.

Each utterance is rendered alone, trimmed to its audible extent, and placed with the composed
pause, so every truth interval is the actual speech extent rather than an estimate.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import soundfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from speech_spans import ExperimentConfig, canonical_checksum, compose_lesson  # noqa: E402

SAMPLE_RATE = 16000
REFERENCE_TEXT = (
    "Good morning. Today we are going to look at a few everyday words, and I will explain when "
    "people actually use them in real conversations."
)
REFERENCE_VOICES = {"samantha": "Samantha", "daniel": "Daniel"}
SAY_REGION = {
    "en": "English (US)",
    "es": "Spanish (Spain)",
    "pt": "Portuguese (Brazil)",
    "fr": "French (France)",
    "it": "Italian (Italy)",
    "de": "German (Germany)",
    "ja": "Japanese (Japan)",
    "ko": "Korean (South Korea)",
    "zh": "Chinese (China mainland)",
}
SAY_FALLBACK = {"ru": "Milena", "hi": "Lekha"}
LATIN_TARGETS = {"es", "pt", "fr", "it", "de"}


def run(command: list[str]) -> None:
    subprocess.run(command, check=True, capture_output=True)


def load_wav(path: Path) -> np.ndarray:
    with tempfile.TemporaryDirectory() as folder:
        out = Path(folder) / "mono.wav"
        run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-i",
                str(path),
                "-ac",
                "1",
                "-ar",
                str(SAMPLE_RATE),
                str(out),
            ]
        )
        data, _rate = soundfile.read(out, dtype="float32")
    return data


def trim(audio: np.ndarray, threshold_db: float = -40.0, pad: float = 0.03) -> np.ndarray:
    """Cut leading and trailing silence relative to the utterance's own peak."""
    if audio.size == 0:
        return audio
    frame = int(0.01 * SAMPLE_RATE)
    frames = audio[: audio.size // frame * frame].reshape(-1, frame)
    rms = np.sqrt((frames**2).mean(axis=1) + 1e-12)
    level = 20 * np.log10(rms / (rms.max() + 1e-12))
    loud = np.flatnonzero(level > threshold_db)
    if loud.size == 0:
        return audio[:0]
    start = max(0, loud[0] * frame - int(pad * SAMPLE_RATE))
    end = min(audio.size, (loud[-1] + 1) * frame + int(pad * SAMPLE_RATE))
    return audio[start:end]


class SystemVoice:
    def __init__(self, family: str, accented: bool) -> None:
        self.family = family
        self.accented = accented

    def voice_for(self, language: str, is_target: bool) -> tuple[str, bool]:
        """Return the ``say`` voice and whether it is a different speaker from the family."""
        spoken = "en" if self.accented and is_target else language
        if spoken in SAY_REGION:
            return f"{self.family} ({SAY_REGION[spoken]})", False
        return SAY_FALLBACK[spoken], True

    def render(self, text: str, language: str, is_target: bool) -> tuple[np.ndarray, dict]:
        voice, confound = self.voice_for(language, is_target)
        with tempfile.TemporaryDirectory() as folder:
            aiff = Path(folder) / "u.aiff"
            run(["say", "-v", voice, "-o", str(aiff), text])
            audio = load_wav(aiff)
        return audio, {"voice": voice, "speaker_change_confound": confound}


class ChatterboxVoice:
    def __init__(self, reference: str, cache: Path) -> None:
        from mlx_audio.tts.utils import load_model

        self.reference = reference
        ref_path = cache / f"reference-{reference}.wav"
        if not ref_path.is_file():
            cache.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory() as folder:
                aiff = Path(folder) / "ref.aiff"
                run(["say", "-v", REFERENCE_VOICES[reference], "-o", str(aiff), REFERENCE_TEXT])
                run(
                    [
                        "ffmpeg",
                        "-v",
                        "error",
                        "-y",
                        "-i",
                        str(aiff),
                        "-ac",
                        "1",
                        "-ar",
                        "24000",
                        str(ref_path),
                    ]
                )
        self.ref_path = ref_path
        self.model = load_model("mlx-community/chatterbox-multilingual-v3")

    def render(self, text: str, language: str, is_target: bool) -> tuple[np.ndarray, dict]:
        from mlx_audio.utils import load_audio

        del is_target
        ref = load_audio(str(self.ref_path), sample_rate=self.model.sample_rate)
        chunks = []
        for result in self.model.generate(
            text=text, ref_audio=ref, lang_code=language, verbose=False, temperature=0.6
        ):
            chunks.append(np.asarray(result.audio, dtype=np.float32))
        audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
        with tempfile.TemporaryDirectory() as folder:
            raw = Path(folder) / "u.wav"
            soundfile.write(raw, audio, self.model.sample_rate)
            audio = load_wav(raw)
        return audio, {"voice": f"chatterbox:{self.reference}", "speaker_change_confound": False}


def make_voice(spec: str, cache: Path) -> Any:
    kind, name = spec.split(":", 1)
    if kind == "system":
        return SystemVoice(name, accented=False)
    if kind == "system-accented":
        return SystemVoice(name, accented=True)
    if kind == "chatterbox":
        return ChatterboxVoice(name, cache)
    raise SystemExit(f"unknown voice spec {spec!r}")


def clip_plan(config: ExperimentConfig) -> list[dict[str, str]]:
    plan = []
    for base, target in config.synth.pairs:
        for voice in config.synth.voices:
            plan.append({"base": base, "target": target, "voice": voice})
        if base == "en" and target in config.synth.accented_targets and target in LATIN_TARGETS:
            family = config.synth.voices[0].split(":", 1)[1]
            plan.append({"base": base, "target": target, "voice": f"system-accented:{family}"})
    return plan


def clip_id(entry: dict[str, str]) -> str:
    voice = entry["voice"].replace(":", "-").lower()
    return f"synth-{entry['base']}-{entry['target']}-{voice}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config-v1.json")
    parser.add_argument("--bank", type=Path, default=HERE / "synthetic/phrasebank-v1.json")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--only", nargs="*", default=None, help="clip ids to (re)render")
    args = parser.parse_args()

    config = ExperimentConfig.model_validate(json.loads(args.config.read_text(encoding="utf-8")))
    bank = json.loads(args.bank.read_text(encoding="utf-8"))
    args.out.mkdir(parents=True, exist_ok=True)
    voices: dict[str, Any] = {}
    manifest = []
    for entry in clip_plan(config):
        identifier = clip_id(entry)
        wav_path = args.out / f"{identifier}.wav"
        truth_path = args.out / f"{identifier}.truth.json"
        if args.only is not None and identifier not in args.only:
            if truth_path.is_file():
                manifest.append(json.loads(truth_path.read_text(encoding="utf-8"))["manifest"])
            continue
        if truth_path.is_file() and wav_path.is_file() and args.only is None:
            manifest.append(json.loads(truth_path.read_text(encoding="utf-8"))["manifest"])
            print(f"skip {identifier}", flush=True)
            continue
        utterances = compose_lesson(
            bank, entry["base"], entry["target"], config.synth.seed, config.synth.blocks_per_clip
        )
        if entry["voice"] not in voices:
            voices[entry["voice"]] = make_voice(entry["voice"], args.out / "_references")
        voice = voices[entry["voice"]]
        pieces: list[np.ndarray] = []
        truth: list[dict[str, Any]] = []
        cursor = 0.0
        confounds = False
        for utterance in utterances:
            is_target = utterance.language == entry["target"]
            audio, meta = voice.render(utterance.text, utterance.language, is_target)
            audio = trim(audio)
            confounds = confounds or meta["speaker_change_confound"]
            gap = np.zeros(int(round(utterance.gap_before * SAMPLE_RATE)), dtype=np.float32)
            pieces.append(gap)
            cursor += gap.size / SAMPLE_RATE
            start = cursor
            pieces.append(audio)
            cursor += audio.size / SAMPLE_RATE
            truth.append(
                {
                    "start": round(start, 4),
                    "end": round(cursor, 4),
                    "language": utterance.language,
                    "is_target": is_target,
                    "role": utterance.role,
                    "bucket": utterance.bucket,
                    "text": utterance.text,
                    "voice": meta["voice"],
                }
            )
        pieces.append(np.zeros(int(0.5 * SAMPLE_RATE), dtype=np.float32))
        signal = np.concatenate(pieces)
        peak = float(np.abs(signal).max()) or 1.0
        signal = (signal / peak * 0.8).astype(np.float32)
        soundfile.write(wav_path, signal, SAMPLE_RATE, subtype="PCM_16")
        record = {
            "clip_id": identifier,
            "path": str(wav_path),
            "target_language": entry["target"],
            "evaluation_only": {
                "base_language": entry["base"],
                "voice": entry["voice"],
                "speaker_change_confound": confounds,
            },
        }
        truth_payload = {
            "manifest": record,
            "duration": round(signal.size / SAMPLE_RATE, 4),
            "utterances": [u.model_dump(mode="json") for u in utterances],
            "truth": truth,
            "composition_checksum": canonical_checksum(
                [u.model_dump(mode="json") for u in utterances]
            ),
        }
        truth_path.write_text(
            json.dumps(truth_payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
        manifest.append(record)
        print(f"rendered {identifier} {signal.size / SAMPLE_RATE:.1f}s", flush=True)
    (args.out / "manifest.json").write_text(
        json.dumps({"clips": manifest}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
