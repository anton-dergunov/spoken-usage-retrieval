import { useEffect, useState } from "react";
import {
  SpeechClipPlayer,
  createSpeechRetrievalClient,
  formatClock,
  HighlightedSourceText,
  type CorpusStatus,
  type MatchMode,
  type SearchResponse,
  type SearchResult,
  type Suggestion,
} from "@spoken-usage-retrieval/react";

const client = createSpeechRetrievalClient({ baseUrl: "/api/v1" });

function SearchIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m21 20-4.7-4.7a7.5 7.5 0 1 0-1 1L20 21l1-1ZM5 10.5a5.5 5.5 0 1 1 11 0 5.5 5.5 0 0 1-11 0Z" /></svg>;
}

function SunIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11 1h2v3h-2V1Zm0 19h2v3h-2v-3ZM3.5 4.9l1.4-1.4L7 5.6 5.6 7 3.5 4.9Zm13.4 13.5 1.4-1.4 2.1 2.1-1.4 1.4-2.1-2.1ZM1 11h3v2H1v-2Zm19 0h3v2h-3v-2ZM3.5 19.1 5.6 17 7 18.4l-2.1 2.1-1.4-1.4ZM16.9 5.6 19 3.5l1.4 1.4L18.4 7l-1.5-1.4ZM12 6a6 6 0 1 1 0 12 6 6 0 0 1 0-12Zm0 2a4 4 0 1 0 0 8 4 4 0 0 0 0-8Z" /></svg>;
}

function MoonIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20.5 15.7A8.5 8.5 0 0 1 8.3 3.5 9 9 0 1 0 20.5 15.7ZM4.8 12a7 7 0 0 1 .8-3.2 10.5 10.5 0 0 0 9.6 9.6A7 7 0 0 1 4.8 12Z" /></svg>;
}

type Theme = "light" | "dark";

function languageName(language: string): string {
  try {
    return new Intl.DisplayNames([navigator.language], { type: "language" }).of(language) ?? language;
  } catch {
    return language;
  }
}

function systemTheme(): Theme {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

function ResultCard({ result, selected, onSelect }: {
  result: SearchResult;
  selected: boolean;
  onSelect(): void;
}) {
  return <button className={`result-card${selected ? " selected" : ""}`} onClick={onSelect} aria-pressed={selected}>
    <span className="result-number" aria-hidden="true">{selected ? "▶" : "○"}</span>
    <span className="result-body">
      <span className="result-sentence"><HighlightedSourceText text={result.sentence} match={result.match} /></span>
      <span className="result-meta">
        <span className="match-type">{result.match_type === "lemma" ? "Related form" : "Exact form"}</span>
        <span>{result.video.channel}</span>
        <span>{languageName(result.source_language)}</span>
        <span>{formatClock(result.clip_start)}</span>
      </span>
    </span>
  </button>;
}

export default function App() {
  const [systemColorScheme, setSystemColorScheme] = useState<Theme>(systemTheme);
  const [themeOverride, setThemeOverride] = useState<Theme | null>(null);
  const [query, setQuery] = useState(() => new URLSearchParams(window.location.search).get("q") ?? "");
  const [language, setLanguage] = useState("");
  const [matchMode, setMatchMode] = useState<MatchMode>("auto");
  const [response, setResponse] = useState<SearchResponse | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [status, setStatus] = useState<CorpusStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const theme = themeOverride ?? systemColorScheme;

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    return () => { delete document.documentElement.dataset.theme; };
  }, [theme]);

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const update = (event: MediaQueryListEvent) => setSystemColorScheme(event.matches ? "dark" : "light");
    media.addEventListener?.("change", update);
    return () => media.removeEventListener?.("change", update);
  }, []);

  useEffect(() => {
    client.status()
      .then((corpusStatus) => {
        setStatus(corpusStatus);
        const requested = new URLSearchParams(window.location.search).get("language");
        if (requested && corpusStatus.indexed_languages.includes(requested)) {
          setLanguage(requested);
          return;
        }
        const enabledIndexed = corpusStatus.enabled_languages.filter((item) =>
          corpusStatus.indexed_languages.includes(item)
        );
        if (enabledIndexed.length === 1) setLanguage(enabledIndexed[0]);
      })
      .catch((reason: Error) => setError(reason.message));
  }, []);

  useEffect(() => {
    setSuggestions([]);
    setResponse(null);
    setSelectedId(null);
    setError("");
    if (!language) return;
    const controller = new AbortController();
    client.suggestions({ language, signal: controller.signal })
      .then(setSuggestions)
      .catch((reason: Error) => {
        if (reason.name !== "AbortError") setError(reason.message);
      });
    return () => controller.abort();
  }, [language]);

  useEffect(() => {
    const trimmed = query.trim();
    if (!trimmed || !language) {
      setResponse(null);
      setSelectedId(null);
      setLoading(false);
      setError("");
      return;
    }
    const controller = new AbortController();
    setResponse(null);
    setSelectedId(null);
    const timer = window.setTimeout(() => {
      setLoading(true);
      setError("");
      client.search({ query: trimmed, language, signal: controller.signal, matchMode })
        .then((next) => {
          if (controller.signal.aborted) return;
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
  }, [query, language, matchMode]);

  const results = response?.results ?? [];
  const selected = results.find((item) => item.occurrence_id === selectedId) ?? results[0] ?? null;

  return <div className="app-shell">
    <header className="topbar">
      <a className="brand" href="/" aria-label="Oído home">
        <span className="brand-glyph">O</span>
        <strong>Oído</strong>
      </a>
      <div className="topbar-tools">
        <button type="button" role="switch" aria-checked={theme === "dark"} className="theme-switch"
          aria-label="Use dark color scheme" title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
          onClick={() => setThemeOverride(theme === "dark" ? "light" : "dark")}>
          <SunIcon /><span className="switch-track"><i /></span><MoonIcon />
        </button>
        <div className="corpus-status">
          <span className={status ? "status-dot ready" : "status-dot"} />
          {status ? <strong>{status.videos} videos</strong> : <span>Loading…</span>}
        </div>
      </div>
    </header>

    <main>
      <section className="search-intro">
        <h1>Find it in <em>real speech.</em></h1>
        <label className="language-picker">
          <span>Source language</span>
          <select value={language} onChange={(event) => setLanguage(event.target.value)}>
            <option value="">Choose a language</option>
            {status?.indexed_languages.map((item) =>
              <option key={item} value={item}>{languageName(item)} ({item})</option>
            )}
          </select>
        </label>
        <label className="language-picker">
          <span>Word forms</span>
          <select value={matchMode} onChange={(event) => setMatchMode(event.target.value as MatchMode)}>
            <option value="auto">All word forms</option>
            <option value="exact">Exact form</option>
            <option value="lemma">Dictionary-form matching</option>
          </select>
        </label>
        <form className="search-box" onSubmit={(event) => event.preventDefault()} role="search">
          <SearchIcon />
          <input autoFocus value={query} onChange={(event) => setQuery(event.target.value)}
            disabled={!language} placeholder={language ? "Search a word or phrase" : "Choose a source language"}
            aria-label="Search source-language speech" />
          {loading ? <span className="search-spinner" aria-label="Searching" /> : query && <button type="button" className="clear-search" onClick={() => setQuery("")} aria-label="Clear search">×</button>}
        </form>
        {!query && suggestions.length > 0 && <div className="suggestions" aria-label="Popular searches">
          {suggestions.map((suggestion) => <button key={`${suggestion.size}:${suggestion.normalized}`}
            onClick={() => setQuery(suggestion.text)}>{suggestion.text}</button>)}
        </div>}
        {query && !language && status && <div className="notice">Choose a source language to search.</div>}
        {response && !response.morphology_available && matchMode === "auto" && <div className="notice">
          Only exact forms are available for this language in the current index.
        </div>}
        {error && <div className="notice error-notice" role="alert">{error}</div>}
      </section>

      {query && language && <div className={`workspace${selected ? "" : " single-column"}`}>
        <section className="results-panel" aria-label="Search results">
          <div className="panel-heading">
            <h2>{response ? <>Results for “{response.query}”</> : "Searching…"}</h2>
            {response && <span className="result-count">{response.total_occurrences} examples</span>}
          </div>
          {query && !loading && response && results.length === 0 && <div className="no-results">
            <strong>{matchMode === "exact" ? "No exact occurrence yet." : "No matching occurrence yet."}</strong>
            <p>Try a shorter phrase.</p>
          </div>}
          <div className="result-list">
            {results.map((result) => <ResultCard key={result.occurrence_id} result={result}
              selected={selected?.occurrence_id === result.occurrence_id}
              onSelect={() => setSelectedId(result.occurrence_id)} />)}
          </div>
        </section>

        {selected && <aside className="viewer-panel">
          <SpeechClipPlayer key={selected.occurrence_id} clip={selected} blind />
        </aside>}
      </div>}
    </main>
  </div>;
}
