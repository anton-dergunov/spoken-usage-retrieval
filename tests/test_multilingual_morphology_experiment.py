from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from speech_retrieval.analysis import Analysis, AnalyzedToken, AnalyzerProvenance

EXPERIMENT = Path(__file__).parents[1] / "experiments/morphological-retrieval-multilingual"
sys.path.insert(0, str(EXPERIMENT))

from compact_index import (  # noqa: E402
    build_database,
    build_streams,
    search_database,
    verify_parity,
)
from morphology_experiment import (  # noqa: E402
    build_training_lexicon,
    parse_conllu,
    result_document,
    score_analysis,
    select_queries,
    strict_key,
)

PROVENANCE = AnalyzerProvenance("fixture", "es", "1", None, {})

TRAIN = """# sent_id = train-1
# text = La casa.
1\tLa\tel\tDET\t_\t_\t0\troot\t_\t_
2\tcasa\tcasa\tNOUN\t_\tNumber=Sing\t1\tdep\t_\tSpaceAfter=No
3\t.\t.\tPUNCT\t_\t_\t1\tpunct\t_\t_

# sent_id = train-2
# text = Él casa.
1\tÉl\tél\tPRON\t_\t_\t0\troot\t_\t_
2\tcasa\tcasar\tVERB\t_\t_\t1\tdep\t_\tSpaceAfter=No
3\t.\t.\tPUNCT\t_\t_\t1\tpunct\t_\t_
"""

TEST = """# sent_id = test-1
# text = Voy al café.
1\tVoy\tir\tVERB\t_\tMood=Ind|Number=Sing\t0\troot\t_\t_
2-3\tal\t_\t_\t_\t_\t_\t_\t_\t_
2\ta\ta\tADP\t_\t_\t1\tdep\t_\t_
3\tel\tel\tDET\t_\t_\t1\tdep\t_\t_
4\tcafé\tcafé\tNOUN\t_\tNumber=Sing\t1\tdep\t_\tSpaceAfter=No
5\t.\t.\tPUNCT\t_\t_\t1\tpunct\t_\t_

# sent_id = test-2
# text = Las casas son casas.
1\tLas\tel\tDET\t_\tNumber=Plur\t0\troot\t_\t_
2\tcasas\tcasa\tNOUN\t_\tNumber=Plur\t1\tdep\t_\t_
3\tson\tser\tAUX\t_\tNumber=Plur\t1\tdep\t_\t_
4\tcasas\tcasa\tNOUN\t_\tNumber=Plur\t1\tdep\t_\tSpaceAfter=No
5\t.\t.\tPUNCT\t_\t_\t1\tpunct\t_\t_
"""


def fixture_analyses() -> list[Analysis]:
    return [
        Analysis(
            (
                AnalyzedToken("Voy", "voy", 0, 3, "ir", "VERB"),
                AnalyzedToken("al", "al", 4, 6, "a", "ADP", word="a", shared_span=True),
                AnalyzedToken("al", "al", 4, 6, "el", "DET", word="el", shared_span=True),
                # Deliberately accent-folded prediction: strict wrong, production key right.
                AnalyzedToken("café", "cafe", 7, 11, "cafe", "NOUN"),
            ),
            PROVENANCE,
        ),
        Analysis(
            (
                AnalyzedToken("Las", "las", 0, 3, "el", "DET"),
                AnalyzedToken("casas", "casas", 4, 9, "casa", "NOUN"),
                AnalyzedToken("son", "son", 10, 13, None, None),
                AnalyzedToken("casas", "casas", 14, 19, "casa", None),
            ),
            PROVENANCE,
        ),
    ]


def test_conllu_reconstructs_unicode_offsets_and_mwt() -> None:
    sentence = parse_conllu(TEST)[0]
    assert sentence.text == "Voy al café."
    assert [(word.form, word.start, word.end) for word in sentence.words[:4]] == [
        ("Voy", 0, 3),
        ("a", 4, 6),
        ("el", 4, 6),
        ("café", 7, 11),
    ]
    assert sentence.words[1].shared_span is True
    assert sentence.words[2].shared_span is True
    assert strict_key("CAFE\u0301") == "café"


def test_conllu_rejects_invalid_source_alignment() -> None:
    with pytest.raises(ValueError, match="Cannot align"):
        parse_conllu(TEST.replace("# text = Voy al café.", "# text = Voy aux café."))


def test_quality_separates_strict_folded_missing_and_nullable_pos() -> None:
    training = parse_conllu(TRAIN)
    test = parse_conllu(TEST)
    scores = score_analysis(test, fixture_analyses(), build_training_lexicon(training))
    assert scores["token_boundary"]["f1"] == 1.0
    assert scores["mwt"] == {"gold": 1, "correct": 1, "accuracy": 1.0}
    assert scores["lemma"]["production_key_accuracy"] > scores["lemma"]["strict_accuracy"]
    assert scores["error_classes"]["missing_lemma"] == 1
    assert scores["lemma"]["by_upos"]["NOUN"]["coverage"] == 1.0


def test_training_ambiguity_oov_and_query_selection_are_deterministic() -> None:
    training = build_training_lexicon(parse_conllu(TRAIN))
    assert len(training["casa"]) == 2
    assert "casas" not in training
    options = {
        "seed": 20260905,
        "max_inflected": 20,
        "minimum_forms": 1,
        "minimum_occurrences": 1,
        "max_ambiguous": 10,
    }
    first = select_queries("es", parse_conllu(TEST), training, **options)
    second = select_queries("es", parse_conllu(TEST), training, **options)
    assert first == second
    assert any(query.intended_lemma == "casa" for query in first)


def test_result_document_has_explicit_unsupported_rows() -> None:
    config = json.loads((EXPERIMENT / "config.json").read_text())
    document = result_document(config)
    assert len(document["quality"]) == 30
    unsupported = {
        row["language"]
        for row in document["quality"]
        if row["analyzer"] == "simplemma" and row["status"] == "N/A"
    }
    assert unsupported == {"ja", "ko", "zh"}


def test_fixture_experiment_reproduces_all_keys_spans_and_routes(tmp_path: Path) -> None:
    sentences = parse_conllu(TEST)
    streams = build_streams("es", sentences, fixture_analyses())
    databases = {}
    for layout in ("dual", "partial", "token"):
        path = tmp_path / f"{layout}.sqlite3"
        metrics = build_database(path, layout, streams)
        assert metrics["size_bytes"] > 0
        databases[layout] = path
    assert verify_parity(databases["dual"], databases["partial"], "partial")["passed"]
    assert verify_parity(databases["dual"], databases["token"], "token")["passed"]
    exact = search_database(databases["token"], "token", "exact", "casas son")
    lemma = search_database(databases["token"], "token", "lemma", "casa ser")
    assert {(match.sentence_id, match.start, match.end) for match in exact} == {("test-2", 4, 13)}
    # Missing lemma for "son" creates a position hole and forbids a false phrase bridge.
    assert lemma == set()
