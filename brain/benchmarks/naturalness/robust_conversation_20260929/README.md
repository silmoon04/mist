# MIST robust conversation cases

Version `2026-09-29.1` freezes 30 multi-turn scenarios for conversational control-flow and continuity failures that broad naturalness prompts can miss. The suite covers floor timing, ASR repair, fact-versus-hypothesis status, implicit and cross-session memory, urgent interruption, delayed/stale background results, return to an older topic, and voice/affect fit.

This is authored benchmark data, **not an overall MIST result**. A direct Cerebras text-only A/A smoke comparison was run on a small supported development subset; semantic judgments are still unrated. This does not exercise the full MIST application, voice path, app tools, audio timing, background jobs or persisted memory. Do not describe it as evidence that MIST is natural, improved, or correct. Meaning and experience judgments remain review work.

## Inventory and partition

The existing naturalness and expectation inventories already cover noisy transcript repair, corrections, fact grounding, explicit and implicit memory boundaries, incomplete turns, delayed-result limits, and voice review as separate evidence layers. This suite reuses those distinctions, and adds scenario sequences for competing speakers, urgent interruption, task revisions, and delayed delivery after the user changes topic. It avoids importing expected replies from prior transcripts. Source inventory files are listed in `cases.json`.

There are 15 development and 15 heldout cases. Development scenario-family IDs are `floor_completion`, `floor_completion_false_end`, `asr_repair`, `asr_repair_names`, `epistemic_status`, `epistemic_uncertainty`, and `implicit_memory`. Heldout families are `urgent_interruption`, `urgent_stop_boundary`, `late_background_result`, `old_topic_return`, `old_topic_return_cross_session`, and `voice_affect_alignment`. The grouping is by task setup and evidence condition, not by trigger word. Heldout cases deliberately use other domains, agents, artifacts and phrasings. The split is visible and is not a secret test set. If any heldout family guides a fix, retire that family from heldout and add a new scenario family before claiming generalization.

The count is 30 cases total (15 development, 15 heldout). Each case has a stable ID, explicit fixture, timestamped scenario timeline, user goal, expected behaviors, objective assertions, acceptable alternatives, critical failures, likely failure seeds, latency metrics and review requirements. Cases do not prescribe a single natural-language answer. Their assertions are semantic contracts; only the separately documented live text subset can be sent through a text adapter, and its conversational judgments remain for independent review.

## Reproduction contract

For full app evaluation, run one case in a fresh MIST session. Preserve its fixture and submit each timeline user event at the stated monotonic offset; between turns, feed forward the real preceding MIST response, tool receipts, task IDs/revisions, and actual event history. Do not inject an idealized assistant transcript or a simulated result as if it came from production. For the cross-session memory case, reset the conversation and retain only the explicitly approved memory fixture. The case file does not include audio recordings. Turn-taking and interruption cases remain unsupported until a real timed audio/event fixture is supplied; transcript replay alone cannot demonstrate acoustic floor control or stop latency.

The narrow direct-model adapter runs supported development cases with sequential text turns and no tools. It starts a new model session per case, uses actual generated replies as context within the case, and does not inject memory or application fixtures. ASR-repair dialogue sent as text tests repair semantics only; it does not test speech recognition. It ignores planned wall-clock offsets, so its latency values are request-to-text measurements, not user-speech or useful-audio latency. Unsupported cases remain visible in `run.json`. The adapter writes a randomized `blind_packet.json` containing only complete paired conversations and an `answer_key.json`; keep the key private from reviewers.

From the repository root, an A/A run can be made with:

```powershell
python -B brain/benchmarks/naturalness/robust_conversation_20260929/runner.py --split development --repeats 1 --output brain/results/robust-conversation-aa-new
```

Use `--a-prompt`, `--b-prompt`, `--a-model`, `--b-model`, `--a-reasoning` or `--b-reasoning` for other paired settings. Choose a new output directory for every run. Live calls require `CEREBRAS_API_KEY` in the environment. The run records source/configuration hashes but never records the key. Run offline checks with `python -B -m unittest brain/benchmarks/naturalness/robust_conversation_20260929/test_runner.py`.

Time origin is `t=0` for the first frozen event. Time fields are milliseconds on a monotonic case clock. For every measured event, record timestamp, source clock, missingness and synchronization uncertainty. In particular, keep these separate:

| Timestamp or span | Definition |
| --- | --- |
| User speech end | Last acoustic sample from the relevant user's utterance; do not use ASR finalization as a proxy. |
| ASR available | Time the final transcript/alternatives became available to the decision path. |
| Turn committed | Time the application committed to respond or yield. |
| First text | First assistant text made available. |
| First useful response | First content that advances the goal, answers, asks a necessary clarification, accepts a consequential correction, or reports a verified current result. A filler alone does not qualify. |
| First audible sample | First sample actually emitted by the speaker, distinct from text generation or an audio packet arriving. |
| Last unwanted audible sample | Last sample emitted after a clear stop request. Measure from interruption onset; include buffered audio. |
| Tool/task events | Call, result, cancellation receipt, revision and delivery times, each with its task/call ID. |

Use a single captured monotonic clock for the endpoints when possible. If clocks differ, record a measured offset and its uncertainty. Missing or negative intervals are unmeasured/invalid; never clamp them to zero. Keep text latency, audible latency, turn-taking, task correctness and human ratings as separate results. Report counts, p50 and tail estimates with uncertainty; 30 scenarios alone do not support a reliable p95 claim. A timeout is a retained failure or censored result, not an omitted sample.

## Evaluation and legitimate alternatives

Run against the real target MIST application/model path if making a product claim. The planned comparison context supplied for this freeze is Qwen3.8-27B at low reasoning on Cerebras, Flux continuation hold of 1200 ms, Jev disabled, Qwen summarization every four turns, and read-only Luna background work limited to two tools. This note records the intended condition; it does not inspect, enforce, or prove that a run used those settings. Each run should capture effective model/provider identifiers, exact prompts/persona and source hashes, concurrency, device/audio route, tool policy, warm/cold status, and this JSON's SHA-256.

Cases marked `timed_multispeaker`, `timed_audio_event`, or `audio_and_blind_review` need real timed audio and playback evidence to support speech timing claims. Cases with `event_fixture` need controllable task completion, cancellation, revisions and delivery receipts. `longitudinal_with_memory_fixture` needs an actual session reset. Unsupported adapter conditions must be marked unsupported, not passed via ordinary chat text. Voice/tone cases require blind listening to complete recorded exchanges; text can only be used to review the semantic content.

Reviewers should see the complete interaction, actual timings and receipts, but not the candidate configuration label. Counterbalance A/B order, permit ties/uncertainty, and collect a short timestamped reason. Apply the per-case `acceptable_alternatives`: e.g. silence can be appropriate after an urgent request for quiet; a clarification can be appropriate where ASR alternatives or prior topics are genuinely ambiguous; an asynchronous result can be queued until the current turn ends. Do not reward literal phrase overlap with evaluator text. Report objective contract failures separately from human semantic judgments and blind listening ratings.

## Freeze and change control

Freeze this case file, evaluation criteria, target configuration, application/model source revisions and run plan before a comparison. Save the exact SHA-256 of `cases.json` alongside each run and retain all failures and timeouts. If any case, criterion, assertion, fixture or timeline changes, increment the suite version and calculate a new hash; never rescore old output as though it used the revised file. If results guide a code or prompt change, report that the affected partition has become development data.

The JSON file SHA-256 at freeze is `8725038c44c933930f9e32ba671bcaa9821d6f982ceeb137b4c64a48bb11c9e1`. A case-file or criterion change requires a new version and hash. The saved live A/A run used Qwen 3.8 27B at low reasoning, the same persona prompt and provider-default tier in both arms. It recorded 9 completed arm samples, one retained `ConnectError` failure (the second arm of the name-repair case stopped at turn 2 of 3), and 20 unsupported arm samples (10 paired cases). Its blind packet contains four complete pairs, with no ratings. These counts describe adapter coverage, not conversational quality. It is a direct-model repeatability sample, not a baseline-versus-change result and not validation of the MIST app. The run was written under `D:\\MIST-recovery-20260929\\benchmark-results\\mist-robust-text-aa-20260929`; that recovery drive became unavailable afterward, so the full transcripts and manifest could not be copied into this workspace.
