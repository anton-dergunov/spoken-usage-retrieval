# English track download coverage

## Question

Can the application depend on YouTube English caption tracks for arbitrary Spanish videos, particularly YouTube's automatically translated English tracks?

## Method

The experiment inspected all 10 videos in the prototype corpus with `yt-dlp`. For each video it recorded separately:

- English tracks listed under creator-authored subtitles;
- English tracks listed under automatic captions;
- whether the preferred English `json3` track could actually be downloaded and parsed.

Creator-authored English was preferred where present. Otherwise, the advertised automatic English track was attempted. Requests were separated by a one-second subtitle delay. A TV client and an embedded-web client were also tried for one failing automatic track.

Run the experiment from the repository root:

```bash
uv run python experiments/english-track-download-coverage/probe_english_captions.py
```

Generated tracks and the JSON report are written to `data/experiments/english-track-download-coverage/`.

## Results

| Measure | Result |
| --- | ---: |
| Videos inspected successfully | 10/10 |
| Videos advertising automatic English | 10/10 |
| Videos advertising creator-authored English | 3/10 |
| Creator-authored English downloads | 3/3 |
| Automatic English downloads attempted | 7 |
| Automatic English downloads successful | 0/7 |
| Automatic English failures returning HTTP 429 | 7/7 |

The one-second delay did not recover automatic translation. The alternate clients did not recover it either: the TV client requested a page reload, while the embedded client reached the caption request and received the same HTTP 429.

## Interpretation

Track discovery and track retrieval are different capabilities. YouTube advertised English for every sampled video, but that did not imply that a backend process could retrieve those tracks reliably.

Current yt-dlp documentation notes that YouTube may require proof-of-origin tokens for subtitle requests from some clients. Those tokens can be video-bound and require additional, evolving infrastructure. See the [yt-dlp PO Token guide](https://github.com/yt-dlp/yt-dlp/wiki/Po-Token-Guide) and the active [subtitle HTTP 429 issue](https://github.com/yt-dlp/yt-dlp/issues/13831).

All requests came from one environment and IP range at one point in time, so the 0/7 result is not a universal availability estimate. It is nevertheless strong evidence that automatic YouTube translation is too fragile to place on the viewer's critical path.

## Decision

- Keep creator-authored English as optional evidence when it downloads successfully.
- Treat automatic YouTube translation as a low-priority, cached experiment rather than a product dependency.
- Never block source-caption ingestion or viewer rendering on English-track retrieval.
- Use cached, on-demand LLM translation as the dependable fallback.
