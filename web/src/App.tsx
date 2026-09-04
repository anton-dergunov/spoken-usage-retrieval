import { useEffect, useState } from "react";
import { getStatus, getSuggestions, searchCorpus } from "./api";
import ClipPlayer, { HighlightedSentence, formatClock } from "./ClipPlayer";
import type { CorpusStatus, SearchResponse, SearchResult, Suggestion } from "./types";

function SearchIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m21 20-4.7-4.7a7.5 7.5 0 1 0-1 1L20 21l1-1ZM5 10.5a5.5 5.5 0 1 1 11 0 5.5 5.5 0 0 1-11 0Z" /></svg>;
}

function ResultCard({ result, selected, onSelect }: {
  result: SearchResult;
  selected: boolean;
  onSelect(): void;
}) {
  const duration = result.clip_end - result.clip_start;
  const boundary = result.boundary.reason === "punctuation" ? "complete sentence" : `${result.boundary.reason} boundary`;
  return <button className={`result-card${selected ? " selected" : ""}`} onClick={onSelect} aria-pressed={selected}>
    <span className="result-number" aria-hidden="true">{selected ? "▶" : "○"}</span>
    <span className="result-body">
      <span className="result-sentence"><HighlightedSentence result={result} /></span>
      <span className="result-meta">
        <span>{result.video.channel}</span>
        <span>{result.video.varieties[0] || "Spanish"}</span>
        <span>{formatClock(result.clip_start)} · {Math.max(1, Math.round(duration))} sec</span>
      </span>
      <span className="result-detail">
        <span>{boundary}</span>
        <span>{result.video.caption_kind === "manual" ? "author captions" : "automatic captions"}</span>
        <span>{Math.round(result.quality_score * 100)}% baseline score</span>
      </span>
    </span>
  </button>;
}

function EmptyViewer() {
  return <section className="viewer-empty">
    <span className="sound-mark" aria-hidden="true"><i /><i /><i /><i /><i /></span>
    <p className="eyebrow">Native speech, precisely located</p>
    <h2>Choose a word.<br />Hear how it lives.</h2>
    <p>Search the local corpus and select an occurrence. The excerpt player will stay here while you compare real uses.</p>
    <div className="empty-key"><kbd>Space</kbd><span>play / pause</span><kbd>R</kbd><span>return to start</span></div>
  </section>;
}

export default function App() {
  const [query, setQuery] = useState(() => new URLSearchParams(window.location.search).get("q") ?? "");
  const [response, setResponse] = useState<SearchResponse | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [status, setStatus] = useState<CorpusStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([getSuggestions(), getStatus()])
      .then(([suggested, corpusStatus]) => {
        setSuggestions(suggested);
        setStatus(corpusStatus);
      })
      .catch((reason: Error) => setError(reason.message));
  }, []);

  useEffect(() => {
    const trimmed = query.trim();
    if (!trimmed) {
      setResponse(null);
      setSelectedId(null);
      setLoading(false);
      setError("");
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setLoading(true);
      setError("");
      searchCorpus(trimmed, controller.signal)
        .then((next) => {
          setResponse(next);
          setSelectedId(next.results[0]?.occurrence_id ?? null);
        })
        .catch((reason: Error) => {
          if (reason.name !== "AbortError") {
            setResponse(null);
            setSelectedId(null);
            setError(reason.message);
          }
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false);
        });
    }, 260);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [query]);

  const results = response?.results ?? [];
  const selected = results.find((item) => item.occurrence_id === selectedId) ?? results[0] ?? null;

  return <div className="app-shell">
    <header className="topbar">
      <a className="brand" href="/" aria-label="Oído home">
        <span className="brand-glyph">O</span>
        <span><strong>Oído</strong><small>native speech index</small></span>
      </a>
      <div className="corpus-status">
        <span className={status ? "status-dot ready" : "status-dot"} />
        {status ? <><strong>{status.videos} videos</strong><span>{status.segments.toLocaleString()} utterances</span></> : <span>Reading local corpus…</span>}
      </div>
    </header>

    <main>
      <section className="search-intro">
        <p className="kicker">Spanish corpus · lexical retrieval prototype</p>
        <h1>Find it in <em>real speech.</em></h1>
        <p className="lede">Search a word or exact phrase, then listen to it in a complete, timestamped utterance.</p>
        <form className="search-box" onSubmit={(event) => event.preventDefault()} role="search">
          <SearchIcon />
          <input autoFocus value={query} onChange={(event) => setQuery(event.target.value)}
            placeholder="Try ‘entonces’ or ‘la verdad’" aria-label="Search Spanish speech" />
          {loading ? <span className="search-spinner" aria-label="Searching" /> : query && <button type="button" className="clear-search" onClick={() => setQuery("")} aria-label="Clear search">×</button>}
          <span className="search-hint">1–5 words</span>
        </form>
        {!query && suggestions.length > 0 && <div className="suggestions" aria-label="Popular searches">
          <span>Explore the corpus</span>
          {suggestions.map((suggestion) => <button key={`${suggestion.size}:${suggestion.normalized}`}
            onClick={() => setQuery(suggestion.text)}>{suggestion.text}</button>)}
        </div>}
        {error && <div className="notice error-notice" role="alert">{error}</div>}
      </section>

      <div className="workspace">
        <section className="results-panel" aria-label="Search results">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">Occurrences</span>
              <h2>{response ? <>Results for “{response.query}”</> : "A small, inspectable corpus"}</h2>
            </div>
            {response && <span className="result-count">{response.total_occurrences} found</span>}
          </div>
          {!query && <div className="corpus-note">
            <span>01</span><p><strong>Start with a suggestion</strong>This prototype indexes contiguous words and phrases while preserving their original timestamps.</p>
          </div>}
          {query && !loading && response && results.length === 0 && <div className="no-results">
            <strong>No exact occurrence yet.</strong>
            <p>Try a shorter form or choose one of the corpus suggestions. Morphological and semantic matching come later.</p>
          </div>}
          <div className="result-list">
            {results.map((result) => <ResultCard key={result.occurrence_id} result={result}
              selected={selected?.occurrence_id === result.occurrence_id}
              onSelect={() => setSelectedId(result.occurrence_id)} />)}
          </div>
        </section>

        <aside className="viewer-panel">
          <div className="viewer-heading">
            <span className="eyebrow">Listening desk</span>
            {selected && <span className="clip-length">{(selected.clip_end - selected.clip_start).toFixed(1)} sec excerpt</span>}
          </div>
          {selected ? <ClipPlayer key={selected.occurrence_id} result={selected} /> : <EmptyViewer />}
          {selected && <p className="player-note">Playback is streamed by YouTube. The custom timeline is limited to this occurrence; source seeking may begin near the closest video keyframe.</p>}
        </aside>
      </div>
    </main>

    <footer><span>Oído / technical feasibility spike</span><span>Exact 1–{status?.max_ngram ?? 5}-gram index · local data</span></footer>
  </div>;
}
