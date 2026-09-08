# Plan 16: Deployment portability

**Status:** Planned

**Depends on:** Plan 10, and Plan 14 for the worker loop it should eventually run under

## Outcome

Run the service and its derived work — alignment first, then audio features — somewhere other than
a developer laptop: a container, a small always-on server, or a home NAS. Latency does not matter
for derived work; predictable resource use does, because the host is running other things that do.

## Current state

- Everything is developed and measured on one MacBook Air M2. The alignment path selects MPS
  automatically, which no deployment target will have.
- Plan 10 measured the CPU-only fallback and found it comfortable: **12.9× realtime on two threads,
  2.18 GB peak RSS** for MMS-300M, essentially the same for the Apache-2.0 XLSR checkpoint. See
  [the forced-alignment report](../../experiments/forced-alignment/README.md).
- Extrapolating to a 4-core Zen NAS (Ryzen V1500B class, 2.0 GHz, AVX2) gives an estimated 4–5×
  realtime on two threads. That is roughly 100× faster than a "10 minutes per audio-minute" budget
  needs, so **CPU throughput is not the constraint**; memory, thermals, and not disturbing the
  host's other work are.
- The `alignment` extra pulls in torch, torchaudio and transformers — about 2.5 GB of wheels — which
  is the dominant cost of a container image, not the model weights.
- `Settings` already has `alignment_device` and `alignment_threads`, so capping is configuration
  rather than new code.
- `ffmpeg`/`ffprobe` are resolved by name from `PATH` and their versions are recorded in clip
  manifests. The developer machine has 4.4.4; the Plan 09 pilot recorded 9.0.1. Any image must pin
  a version and record it, because clip preparation uses `-accurate_seek` and validates duration
  against a tolerance.

## Decisions

- **Containerize, do not port.** Synology DSM ships an older glibc than the PyTorch wheels expect.
  Running inside Docker (Container Manager) sidesteps the platform question entirely and is the
  only configuration this plan supports. x86-64 with AVX2 is a requirement; ARM-based NAS models are
  explicitly out of scope.
- **Add an ONNX Runtime emission backend behind the existing `CtcEmissionBackend` protocol.** Plan
  10 deliberately built one algorithm over a swappable emissions source for this. ONNX Runtime plus
  an int8-quantized encoder replaces ~2.5 GB of torch with ~300 MB of runtime and roughly halves
  resident memory. Treat it as an optimization to be *measured*, not assumed: quantization changes
  emissions, so it must be scored against the PyTorch path on the Plan 10 sample before being
  recommended, and the report must state the quality delta in milliseconds.
- **Two images, not one.** A slim service image with no ML dependencies, and a worker image with the
  derived-work extras. The subtitle-only baseline must remain deployable without either model stack,
  which is the same no-model-baseline rule the rest of the roadmap follows.
- **Resource caps are part of the contract, not advice.** One worker process, `--cpus` and
  `--memory` limits, `alignment_threads` set explicitly, and a nice level. A background job that can
  starve the host's other services is a defect.
- **Chunking stays mandatory.** wav2vec2 self-attention is quadratic in frames, so a whole video in
  one pass exhausts memory regardless of the host. `MAX_WINDOW_SECONDS` already enforces this; the
  deployment must not raise it.
- **The data directory is a volume with a documented layout.** Raw captions and audio are immutable
  and expensive to re-acquire; derived clips, indexes, and alignment caches are replaceable. Back up
  the first, and let the second be rebuilt.
- **No orchestration.** No Kubernetes, no message broker, no multi-host execution. One host, one
  worker, restart policies from the container runtime.

## Implementation work

1. Add a `Dockerfile` producing the slim service image: the package, no extras, a pinned ffmpeg, a
   non-root user, and a healthcheck that calls `doctor`.
2. Add the worker image variant with the `alignment` extra and a documented model-cache volume, so a
   restart does not re-download 2.5 GB of weights.
3. Add `OnnxCtcBackend` implementing `CtcEmissionBackend`, plus an export script that converts a
   checkpoint and records the exporting versions in provenance. Provenance must distinguish an ONNX
   int8 result from a PyTorch result, because they are not bit-identical.
4. Extend the Plan 10 experiment with an `onnx` system so the quantization delta is measured on the
   same sample and the same reference, rather than in a separate benchmark.
5. Add a compose file with the caps above, and a worker entrypoint that processes derived work in a
   bounded loop and shuts down on a signal. Connect it to Plan 14's queue if that exists by then;
   otherwise a simple bounded pass.
6. Document the operational surface: volumes and which are precious, environment variables, expected
   throughput and memory per worker, how to check progress, and how to stop derived work without
   stopping search.
7. Record measured CPU-only throughput and peak RSS on the actual target hardware, replacing Plan
   10's extrapolation with a measurement.

## Public interfaces and data

- No change to the Python, HTTP, or React contracts. This plan changes where the code runs, not what
  it returns.
- `OnnxCtcBackend` satisfies the existing `CtcEmissionBackend` protocol; `Aligner` is unchanged.
- Alignment provenance gains the runtime and quantization used, alongside the model license it
  already records.
- The image tags, volume layout, and environment variables become a documented interface.

## Acceptance tests and verification

- Both images build and start; the slim image runs search with no ML dependencies installed.
- `doctor` inside each image reports honestly, including the alignment model and **its license**.
- The ONNX backend produces group sequences satisfying the same invariants as the PyTorch path, and
  the unit fixtures run against both backends.
- The measured quality delta between ONNX int8 and PyTorch is recorded in the Plan 10 report; if it
  is large enough to matter, int8 is not recommended and the reason is published.
- A worker held at its CPU and memory caps completes a bounded batch without exceeding them, and
  stops cleanly on SIGTERM.
- Restarting the worker reuses the model cache rather than re-downloading.
- Search stays responsive while derived work runs.

## Non-goals

- Multi-host or GPU deployment, autoscaling, or a hosted service.
- ARM NAS hardware, or any host without AVX2.
- Publishing images to a public registry.
- Making alignment mandatory anywhere, or bundling model weights into an image.
- Replacing Plan 14's queue with a deployment-specific scheduler.
