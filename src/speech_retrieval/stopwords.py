"""Small curated function-word lists for suggestions only, never search filtering.

These deliberately conservative resources are maintained with the code. An absent
language has no stopwords. Keys use the same accent folding as the surface index.
"""

from functools import lru_cache

from .text import normalize_token

_WORDS = {
    "en": "a an the and or but of to in on at for from with by is are was were be been being it this that these those i you he she we they my your his her our their",
    "es": "el la los las un una unos unas y o pero de del a al en por para con sin es son era ser se lo le les me te nos os mi tu su sus que como",
    "pt": "o a os as um uma uns umas e ou mas de do da dos das em no na nos nas por para com sem é são ser se que",
    "fr": "le la les un une des du de à au aux et ou mais en pour par avec sans est sont être se ce cette ces je tu il elle nous vous ils elles",
    "de": "der die das den dem des ein eine einer einen einem eines und oder aber in im am an auf von zu zum zur mit für ist sind war sein es ich du er sie wir ihr",
    "it": "il lo la i gli le un uno una e o ma di del della dei delle a al alla in con per da è sono essere si che",
    "hi": "और या का की के को में से पर है हैं था थे यह वह एक",
    "ja": "の は が を に へ と で も や です ます",
    "ko": "은 는 이 가 을 를 에 의 와 과 도",
    "zh": "的 了 在 是 和 与 及 或 把 被 这 那 一个",
}


@lru_cache(maxsize=64)
def stopwords(language: str) -> frozenset[str]:
    return frozenset(
        normalize_token(word) for word in _WORDS.get(language.split("-")[0], "").split()
    )
