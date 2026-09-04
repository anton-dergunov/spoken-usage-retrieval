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
  return <button className={`result-card${selected ? " selected" : ""}`} onClick={onSelect} aria-pressed={selected}>
    <span className="result-number" aria-hidden="true">{selected ? "▶" : "○"}</span>
    <span className="result-body">
      <span className="result-sentence"><HighlightedSentence result={result} /></span>
      <span className="result-meta">
        <span>{result.video.channel}</span>
        <span>{formatClock(result.clip_start)}</span>
      </span>
    </span>
  </button>;
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
        <strong>Oído</strong>
      </a>
      <div className="corpus-status">
        <span className={status ? "status-dot ready" : "status-dot"} />
        {status ? <strong>{status.videos} videos</strong> : <span>Loading…</span>}
      </div>
    </header>

    <main>
      <section className="search-intro">
        <h1>Find it in <em>real speech.</em></h1>
        <form className="search-box" onSubmit={(event) => event.preventDefault()} role="search">
          <SearchIcon />
          <input autoFocus value={query} onChange={(event) => setQuery(event.target.value)}
            placeholder="Try ‘entonces’ or ‘la verdad’" aria-label="Search Spanish speech" />
          {loading ? <span className="search-spinner" aria-label="Searching" /> : query && <button type="button" className="clear-search" onClick={() => setQuery("")} aria-label="Clear search">×</button>}
        </form>
        {!query && suggestions.length > 0 && <div className="suggestions" aria-label="Popular searches">
          {suggestions.map((suggestion) => <button key={`${suggestion.size}:${suggestion.normalized}`}
            onClick={() => setQuery(suggestion.text)}>{suggestion.text}</button>)}
        </div>}
        {error && <div className="notice error-notice" role="alert">{error}</div>}
      </section>

      {query && <div className={`workspace${selected ? "" : " single-column"}`}>
        <section className="results-panel" aria-label="Search results">
          <div className="panel-heading">
            <h2>{response ? <>Results for “{response.query}”</> : "Searching…"}</h2>
            {response && <span className="result-count">{response.total_occurrences} examples</span>}
          </div>
          {query && !loading && response && results.length === 0 && <div className="no-results">
            <strong>No exact occurrence yet.</strong>
            <p>Try a shorter phrase.</p>
          </div>}
          <div className="result-list">
            {results.map((result) => <ResultCard key={result.occurrence_id} result={result}
              selected={selected?.occurrence_id === result.occurrence_id}
              onSelect={() => setSelectedId(result.occurrence_id)} />)}
          </div>
        </section>

        {selected && <aside className="viewer-panel">
          <ClipPlayer key={selected.occurrence_id} result={selected} />
        </aside>}
      </div>}
    </main>
  </div>;
}
