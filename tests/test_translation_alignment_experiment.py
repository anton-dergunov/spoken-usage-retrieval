import importlib.util
import json
from collections import Counter
from pathlib import Path

DATASET = (
    Path(__file__).parents[1]
    / "experiments"
    / "target-language-word-alignment"
    / "challenge-set-v1.jsonl"
)


def test_alignment_challenge_set_has_balanced_locked_splits_and_valid_spans():
    cases = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines()]
    assert len(cases) == 50
    assert Counter(case["source_language"] for case in cases) == {
        "es": 10,
        "de": 10,
        "ru": 10,
        "zh-Hans": 10,
        "ja": 10,
    }
    assert Counter(case["split"] for case in cases) == {"dev": 10, "test": 40}
    assert len({case["id"] for case in cases}) == 50

    for case in cases:
        for side in ("source", "target"):
            text = case[f"{side}_text"]
            token_ids = []
            previous_end = 0
            for token in case[f"{side}_tokens"]:
                start, end = token["range"]["start"], token["range"]["end"]
                assert 0 <= previous_end <= start < end <= len(text)
                assert text[start:end] == token["text"]
                token_ids.append(token["id"])
                previous_end = end
            assert token_ids == [
                f"{'S' if side == 'source' else 'T'}{index}"
                for index in range(1, len(token_ids) + 1)
            ]
        source_ids = {token["id"] for token in case["source_tokens"]}
        target_ids = {token["id"] for token in case["target_tokens"]}
        links = {tuple(edge) for edge in [*case["sure_links"], *case["possible_links"]]}
        assert links
        assert all(source in source_ids and target in target_ids for source, target in links)
        assert not ({source for source, _ in links} & set(case["unaligned_source_ids"]))
        assert not ({target for _, target in links} & set(case["unaligned_target_ids"]))


def test_optional_pharaoh_reader_keeps_sure_and_possible_links_distinct(tmp_path):
    module_path = DATASET.with_name("benchmark_io.py")
    spec = importlib.util.spec_from_file_location("alignment_benchmark_io", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source, target, links = (tmp_path / name for name in ("source.txt", "target.txt", "links.txt"))
    source.write_text("uno dos\n", encoding="utf-8")
    target.write_text("one two\n", encoding="utf-8")
    links.write_text("0-0 1?1\n", encoding="utf-8")
    examples = list(module.read_pharaoh(source, target, links))
    assert examples[0]["sure_links"] == [(0, 0)]
    assert examples[0]["possible_links"] == [(1, 1)]
