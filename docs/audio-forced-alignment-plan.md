# Audio forced-alignment plan

## Goal

Add useful word timing when YouTube supplies only sentence- or cue-level timestamps, especially for manually authored captions.

## Proposed path

1. Run WhisperX on the small audio interval around a selected sentence, rather than processing a full video by default.
2. Align the known Spanish sentence to WhisperX word timestamps and retain confidence plus unmatched-token information.
3. Cache alignment by video ID, audio interval, transcript hash, model version, and alignment settings.
4. Fall back to the current cue-level display whenever audio cannot be accessed or alignment confidence is poor.
5. Compare timestamps against the existing automatic-caption segments before enabling progressive word highlighting for manual captions.

Montreal Forced Aligner remains a later accuracy benchmark, but its models and runtime make WhisperX the more practical first experiment. Exact phoneme alignment is unnecessary for the initial visual progression; stable word-group timing is sufficient.
