"""Gold-data and quality helpers for the multilingual morphology experiment."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from speech_retrieval.analysis import Analysis, AnalyzedToken
from speech_retrieval.text import normalize_token

LEXICAL_UPOS = frozenset({"ADJ", "ADV", "AUX", "NOUN", "PRON", "VERB"})


@dataclass(frozen=True)
class GoldWord:
    id: int
    form: str
    lemma: str | None
    upos: str | None
    features: dict[str, str] | None
    start: int
    end: int
    source_surface: str
    shared_span: bool = False


@dataclass(frozen=True)
class GoldSentence:
    sentence_id: str
    text: str
    words: tuple[GoldWord, ...]
    source_tokens: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class Query:
    query_id: str
    surface: str
    intended_lemma: str
    selection_class: str
    observed_forms: tuple[str, ...]


def strict_key(value: str | None) -> str | None:
    """Normalize for strict linguistic scoring without removing accents."""
    if value is None or value == "_" or not value.strip():
        return None
    return unicodedata.normalize("NFC", value).casefold()


def production_key(value: str | None) -> str | None:
    if value is None or value == "_" or not value.strip():
        return None
    return normalize_token(value)


def parse_features(value: str) -> dict[str, str] | None:
    if value == "_":
        return None
    return dict(part.split("=", 1) for part in value.split("|") if "=" in part)


def _align_surface(text: str, form: str, cursor: int) -> tuple[int, int]:
    while cursor < len(text) and text[cursor].isspace():
        cursor += 1
    if text.startswith(form, cursor):
        return cursor, cursor + len(form)
    # UD's SpaceAfter and Typo annotations occasionally make direct cursor alignment
    # insufficient. A bounded forward search still preserves canonical source offsets.
    found = text.find(form, cursor, min(len(text), cursor + max(64, len(form) * 4)))
    if found < 0:
        raise ValueError(f"Cannot align CoNLL-U token {form!r} after character {cursor}")
    return found, found + len(form)


def parse_conllu(content: str) -> list[GoldSentence]:
    """Parse CoNLL-U and reconstruct source spans, including shared MWT spans."""
    sentences: list[GoldSentence] = []
    blocks = [block for block in content.split("\n\n") if block.strip()]
    for block_number, block in enumerate(blocks, start=1):
        metadata: dict[str, str] = {}
        rows: list[list[str]] = []
        for line in block.splitlines():
            if line.startswith("# ") and " = " in line:
                key, value = line[2:].split(" = ", 1)
                metadata[key] = value
            elif line and not line.startswith("#"):
                fields = line.split("\t")
                if len(fields) != 10:
                    raise ValueError(f"Invalid CoNLL-U row with {len(fields)} columns")
                rows.append(fields)
        text = metadata.get("text")
        if text is None:
            raise ValueError("CoNLL-U sentence is missing # text")
        sentence_id = metadata.get("sent_id", str(block_number))

        ranges: dict[int, tuple[int, int, int, str]] = {}
        cursor = 0
        source_spans: list[tuple[int, int]] = []
        direct_spans: dict[int, tuple[int, int, str]] = {}
        for fields in rows:
            token_id, form, misc = fields[0], fields[1], fields[9]
            if "." in token_id:
                continue
            if "-" in token_id:
                first, last = (int(value) for value in token_id.split("-", 1))
                start, end = _align_surface(text, form, cursor)
                source_spans.append((start, end))
                for word_id in range(first, last + 1):
                    ranges[word_id] = (start, end, last - first + 1, form)
                cursor = end if "SpaceAfter=No" in misc else end
                continue
            word_id = int(token_id)
            if word_id in ranges:
                continue
            start, end = _align_surface(text, form, cursor)
            direct_spans[word_id] = (start, end, form)
            source_spans.append((start, end))
            cursor = end if "SpaceAfter=No" in misc else end

        words: list[GoldWord] = []
        for fields in rows:
            token_id = fields[0]
            if "-" in token_id or "." in token_id:
                continue
            word_id = int(token_id)
            form, lemma, upos, feats = fields[1], fields[2], fields[3], fields[5]
            if word_id in ranges:
                start, end, count, surface = ranges[word_id]
                shared = count > 1
            else:
                start, end, surface = direct_spans[word_id]
                shared = False
            words.append(
                GoldWord(
                    word_id,
                    form,
                    None if lemma == "_" else lemma,
                    None if upos == "_" else upos,
                    parse_features(feats),
                    start,
                    end,
                    surface,
                    shared,
                )
            )
        sentences.append(
            GoldSentence(sentence_id, text, tuple(words), tuple(dict.fromkeys(source_spans)))
        )
    return sentences


def read_conllu(path: Path) -> list[GoldSentence]:
    return parse_conllu(path.read_text(encoding="utf-8"))


def build_training_lexicon(
    sentences: Iterable[GoldSentence],
) -> dict[str, set[tuple[str, str | None]]]:
    lexicon: dict[str, set[tuple[str, str | None]]] = defaultdict(set)
    for sentence in sentences:
        for word in sentence.words:
            surface, lemma = strict_key(word.form), strict_key(word.lemma)
            if surface and lemma and word.upos not in {"PUNCT", "SYM"}:
                lexicon[surface].add((lemma, word.upos))
    return dict(lexicon)


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _lexical_gold(sentence: GoldSentence) -> list[GoldWord]:
    return [word for word in sentence.words if word.upos not in {"PUNCT", "SYM"}]


def _group_by_span(items: Iterable[Any]) -> dict[tuple[int, int], list[Any]]:
    grouped: dict[tuple[int, int], list[Any]] = defaultdict(list)
    for item in items:
        grouped[(item.start, item.end)].append(item)
    return grouped


def score_analysis(
    sentences: Iterable[GoldSentence],
    analyses: Iterable[Analysis],
    training_lexicon: dict[str, set[tuple[str, str | None]]],
    *,
    max_examples: int = 25,
) -> dict[str, Any]:
    """Score offsets, boundaries, expansion, and lemmas from the production adapter."""
    totals: Counter[str] = Counter()
    by_upos: dict[str, Counter[str]] = defaultdict(Counter)
    examples: list[dict[str, Any]] = []
    error_classes: Counter[str] = Counter()
    for sentence, analysis in zip(sentences, analyses, strict=True):
        analysis.validate(sentence.text)
        gold_words = _lexical_gold(sentence)
        gold_by_span = _group_by_span(gold_words)
        predicted_by_span = _group_by_span(analysis.tokens)
        gold_boundaries = set(gold_by_span)
        predicted_boundaries = set(predicted_by_span)
        totals["gold_boundaries"] += len(gold_boundaries)
        totals["predicted_boundaries"] += len(predicted_boundaries)
        totals["correct_boundaries"] += len(gold_boundaries & predicted_boundaries)

        gold_mwt = {span: words for span, words in gold_by_span.items() if len(words) > 1}
        totals["gold_mwt"] += len(gold_mwt)
        for span, words in gold_mwt.items():
            predicted = predicted_by_span.get(span, [])
            if [strict_key(token.word or token.surface) for token in predicted] == [
                strict_key(word.form) for word in words
            ]:
                totals["correct_mwt"] += 1
            else:
                error_classes["mwt_mismatch"] += 1

        for span, words in gold_by_span.items():
            predicted = predicted_by_span.get(span, [])
            if len(predicted) != len(words):
                error_classes["tokenization_mismatch"] += abs(len(words) - len(predicted)) or 1
            for position, gold in enumerate(words):
                totals["gold_words"] += 1
                category = by_upos[gold.upos or "NULL"]
                category["gold"] += 1
                surface = strict_key(gold.form)
                candidates = training_lexicon.get(surface or "", set())
                is_unseen = not candidates
                is_ambiguous = len(candidates) > 1
                if is_unseen:
                    totals["unseen_gold"] += 1
                if is_ambiguous:
                    totals["ambiguous_gold"] += 1
                if position >= len(predicted):
                    error_classes["tokenization_mismatch"] += 1
                    continue
                token: AnalyzedToken = predicted[position]
                totals["aligned_words"] += 1
                category["aligned"] += 1
                predicted_strict, gold_strict = strict_key(token.lemma), strict_key(gold.lemma)
                predicted_folded, gold_folded = (
                    production_key(token.lemma),
                    production_key(gold.lemma),
                )
                if predicted_strict is None:
                    error_classes["missing_lemma"] += 1
                    continue
                totals["lemma_predictions"] += 1
                category["predicted"] += 1
                strict_correct = predicted_strict == gold_strict
                folded_correct = predicted_folded == gold_folded
                totals["strict_correct"] += strict_correct
                totals["production_correct"] += folded_correct
                category["strict_correct"] += strict_correct
                category["production_correct"] += folded_correct
                if is_unseen:
                    totals["unseen_predicted"] += 1
                    totals["unseen_correct"] += strict_correct
                if is_ambiguous:
                    totals["ambiguous_predicted"] += 1
                    totals["ambiguous_correct"] += strict_correct
                    if not strict_correct:
                        error_classes["ambiguous_one_result_guess"] += 1
                if not strict_correct:
                    error_classes["wrong_lemma"] += 1
                    if len(examples) < max_examples:
                        examples.append(
                            {
                                "form": gold.form,
                                "gold_lemma": gold.lemma,
                                "predicted_lemma": token.lemma,
                                "gold_upos": gold.upos,
                                "predicted_upos": token.upos,
                                "unseen": is_unseen,
                                "ambiguous": is_ambiguous,
                            }
                        )

    boundary_precision = _rate(totals["correct_boundaries"], totals["predicted_boundaries"])
    boundary_recall = _rate(totals["correct_boundaries"], totals["gold_boundaries"])
    boundary_f1 = (
        round(2 * boundary_precision * boundary_recall / (boundary_precision + boundary_recall), 6)
        if boundary_precision and boundary_recall
        else 0.0
    )
    upos_rows = {
        upos: {
            "gold": counts["gold"],
            "coverage": _rate(counts["predicted"], counts["gold"]),
            "strict_accuracy": _rate(counts["strict_correct"], counts["predicted"]),
            "production_key_accuracy": _rate(counts["production_correct"], counts["predicted"]),
        }
        for upos, counts in sorted(by_upos.items())
    }
    return {
        "gold_words": totals["gold_words"],
        "aligned_words": totals["aligned_words"],
        "token_boundary": {
            "precision": boundary_precision,
            "recall": boundary_recall,
            "f1": boundary_f1,
        },
        "mwt": {
            "gold": totals["gold_mwt"],
            "correct": totals["correct_mwt"],
            "accuracy": _rate(totals["correct_mwt"], totals["gold_mwt"]),
        },
        "lemma": {
            "coverage": _rate(totals["lemma_predictions"], totals["gold_words"]),
            "strict_accuracy": _rate(totals["strict_correct"], totals["lemma_predictions"]),
            "production_key_accuracy": _rate(
                totals["production_correct"], totals["lemma_predictions"]
            ),
            "unseen_coverage": _rate(totals["unseen_predicted"], totals["unseen_gold"]),
            "unseen_accuracy": _rate(totals["unseen_correct"], totals["unseen_predicted"]),
            "ambiguous_coverage": _rate(totals["ambiguous_predicted"], totals["ambiguous_gold"]),
            "ambiguous_accuracy": _rate(totals["ambiguous_correct"], totals["ambiguous_predicted"]),
            "by_upos": upos_rows,
        },
        "error_classes": dict(sorted(error_classes.items())),
        "error_examples": examples,
    }


def _stable_order(seed: int, language: str, *parts: str) -> str:
    payload = "\0".join((str(seed), language, *parts))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def select_queries(
    language: str,
    test_sentences: Iterable[GoldSentence],
    training_lexicon: dict[str, set[tuple[str, str | None]]],
    *,
    seed: int,
    max_inflected: int = 20,
    minimum_forms: int = 2,
    minimum_occurrences: int = 5,
    max_ambiguous: int = 10,
) -> list[Query]:
    """Select a deterministic, recorded query manifest from test gold words."""
    lemma_forms: dict[str, Counter[str]] = defaultdict(Counter)
    form_lemmas: dict[str, Counter[str]] = defaultdict(Counter)
    display: dict[str, str] = {}
    for sentence in test_sentences:
        for word in sentence.words:
            form, lemma = strict_key(word.form), strict_key(word.lemma)
            if word.upos not in LEXICAL_UPOS or not form or not lemma:
                continue
            lemma_forms[lemma][form] += 1
            form_lemmas[form][lemma] += 1
            display.setdefault(form, word.form)

    regular = [
        (lemma, counts)
        for lemma, counts in lemma_forms.items()
        if len(counts) >= minimum_forms and sum(counts.values()) >= minimum_occurrences
    ]
    regular.sort(key=lambda row: _stable_order(seed, language, "inflected", row[0]))
    selected: list[Query] = []
    for lemma, counts in regular[:max_inflected]:
        nonlemma = [item for item in counts.items() if item[0] != lemma]
        surface = min(nonlemma or list(counts.items()), key=lambda item: (-item[1], item[0]))[0]
        selected.append(
            Query(
                f"{language}:inflected:{lemma}",
                display[surface],
                lemma,
                "inflected_lemma",
                tuple(sorted(counts)),
            )
        )

    ambiguous_forms = [
        form
        for form, candidates in training_lexicon.items()
        if len({lemma for lemma, _upos in candidates}) > 1 and form in form_lemmas
    ]
    ambiguous_forms.sort(key=lambda form: _stable_order(seed, language, "ambiguous", form))
    for form in ambiguous_forms[:max_ambiguous]:
        for intended in sorted(form_lemmas[form]):
            selected.append(
                Query(
                    f"{language}:ambiguous:{form}:{intended}",
                    display[form],
                    intended,
                    "ambiguous_form",
                    (form,),
                )
            )
    return selected


def result_document(config: dict[str, Any]) -> dict[str, Any]:
    """Create all required rows, including explicit unsupported combinations."""
    rows = []
    for language in config["languages"]:
        for analyzer in ("unicode", "simplemma", "stanza"):
            supported = analyzer != "simplemma" or language["simplemma"]
            rows.append(
                {
                    "language": language["tag"],
                    "treebank": language["treebank"],
                    "analyzer": analyzer,
                    "status": "pending" if supported else "N/A",
                    "reason": None if supported else "simplemma does not support this language",
                }
            )
    return {
        "schema_version": config["schema_version"],
        "experiment": config["experiment"],
        "configuration_sha256": hashlib.sha256(
            json.dumps(config, sort_keys=True).encode("utf-8")
        ).hexdigest(),
        "quality": rows,
        "queries": [],
        "retrieval": [],
        "storage": [],
        "diagnostics": [],
        "inputs": {},
    }


def dataclass_rows(items: Iterable[Any]) -> list[dict[str, Any]]:
    return [asdict(item) for item in items]
