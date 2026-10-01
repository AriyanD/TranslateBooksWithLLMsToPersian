# Working Backlog

Prioritized, verified triage of every open issue, pull request and discussion as of 2026-08-01.

Every item below was checked against the code at `main` (`2827ed5`). Line references were valid at that
commit; re-check them if you pick up an item much later. Items already fixed in code are listed in
[Housekeeping](#housekeeping) rather than silently dropped.

**How to use this document.** Work top to bottom. Each item is a self-contained card: symptom, verified
evidence, fix direction, done-criteria. Tick the checkbox and add the PR number when it lands. Do not
reorder sprints without a reason written into the item.

**Effort key.** `S` under an hour. `M` a few hours. `L` multi-day.

---

## Table of contents

- [Sprint 1 — correctness and noise reduction](#sprint-1--correctness-and-noise-reduction)
- [Sprint 2 — security and data integrity](#sprint-2--security-and-data-integrity)
- [Sprint 3 — provider layer and config plumbing](#sprint-3--provider-layer-and-config-plumbing)
- [Sprint 4 — i18n and technical debt](#sprint-4--i18n-and-technical-debt)
- [Feature queue](#feature-queue)
- [Pull requests](#pull-requests)
- [Housekeeping](#housekeeping)
- [Deferred and product decisions](#deferred-and-product-decisions)
- [Cross-cutting notes](#cross-cutting-notes)

---

## Sprint 1 — correctness and noise reduction

Goal: stop reporting success when the output is incomplete, stop generating support tickets for trivial
causes, and unblock the UI. Everything here is small or medium effort with directly visible user impact.

### [x] 1.1 — Issue #239 (commit `3d310be`): jobs marked `completed` while chunks stayed in the source language

**Symptom.** A book finishes as "Complete", but scattered paragraphs are still in the source language.
No partial-completion card is shown and no resume option is offered, because the checkpoint has already
been deleted.

**Verified cause.** When a chunk exhausts all retries, the EPUB path falls back to the source text and
increments a *separate* counter at `src/core/epub/xhtml_translator.py:565` (`stats.fallback_used`).
The completion classifier at `src/api/handlers.py:611` and `:621` only inspects `stats.failed_chunks`,
which fallback chunks never touch. The job is therefore classified `completed`, and
`checkpoint_manager.cleanup_completed_job` (`src/api/handlers.py:637`) destroys the resume data.

This is the unfixed half of #239. The other half, web-scraping boilerplate leaking into the
translation, was fixed by PR #240 (`2827ed5`).

**Fix direction.** Make the completion classifier consider fallback chunks as unfinished work: treat
`failed_chunks + fallback_used > 0` as `partial`. Keep the two counters distinct in the stats payload so
the UI can distinguish "failed outright" from "left as source text", and do not delete the checkpoint
for a `partial` job. The partial-completion card added in `55efe12` already renders correctly once the
status is right.

**Done when.** A run where at least one chunk exhausts its retries ends as `partial`, keeps its
checkpoint, offers resume, and the completion card names the number of untranslated chunks. Add a unit
test that drives the classifier with `fallback_used=1, failed_chunks=0`.

**Shipped.** Commit `3d310be`: the decision moved out of `handlers.py` into
`src/api/completion_status.py::classify_completion`, which returns `partial` when a chunk fell back to
its source text, and `src/api/handlers.py` now keeps the checkpoint for `partial` and `error`. Covered by
`tests/unit/test_completion_status.py`. Rule 4 of the classifier has since moved from `fallback_used` to
the `unfinished_chunks` counter (item 1.8, design decision D9), so a job that retried every fallen-back
chunk successfully can reach `completed`; `fallback_used` stays the input when that key is absent, which
is the regression net for this item. Making the kept checkpoint actually usable — retrying those chunks —
is item 1.8.

**Effort.** S/M. **Blocks.** Item 1.2, item 5.5.

---

### [x] 1.2 — Issue #246 (commit `3d310be`): UI reports success, download fails, output directory is empty

**Symptom.** Reported in Chinese: the interface shows the translation as finished, downloading raises an
error, and the output directory turns out to be empty.

**Status.** Unconfirmed, no repro attached, zero maintainer comments so far. Strongly suspected to be a
second symptom of the same status-classification defect as item 1.1: a job is marked complete and its
checkpoint cleaned up before the output file was actually assembled on disk.

**Fix direction.** Do item 1.1 first, then ask the reporter to retest. If it still occurs, the remaining
suspect is an exception thrown between "mark completed" and final file assembly, which would leave the
DB row consistent and the filesystem empty. Add a post-assembly existence check before a job is allowed
to reach `completed`: if the output path does not exist or is zero bytes, classify as `error` and keep
the checkpoint.

**Done when.** Either the reporter confirms it is gone after 1.1, or a repro is obtained and the
existence guard is in place with a test.

**Shipped.** The existence guard landed with 1.1 in commit `3d310be`: rules 2 and 3 of
`classify_completion` return `error` when the output path is missing, or exists at 0 bytes with
`total_chunks > 0`, and `handlers.py` keeps the checkpoint in that case. A job can no longer reach
`completed` with nothing on disk, which is the reported symptom. Covered by
`tests/unit/test_completion_status.py`. The reporter's retest is still outstanding; if it recurs, the
remaining suspect is unchanged — an exception thrown between "mark completed" and final assembly.

**Effort.** S once 1.1 lands. **Depends on.** Item 1.1.

---

### [x] 1.8 — Issue #261 (plan `plan/PLAN_Issue261_RetryFailedChunks.md`): a `partial` job could never be finished

Numbered 1.8 because that is the next free number in this sprint, but placed here on purpose: #239, #246
and #261 are three faces of one defect ("we announce a success that is not one"), and splitting them
across the file costs more than the out-of-order number.

**Symptom.** Once items 1.1/1.2 made a run with fallback chunks end as `partial`, that job became
unfinishable. Resume translated nothing and the card said "0 failed", so the user had no way to recover
the paragraphs left in the source language and no information about which ones they were.

**Verified cause.** Three defects stacked. The EPUB resume pointer is a *file* index, so the file holding
a fallen-back chunk counted as finished (the TXT/SRT path already derived pending work from per-unit
statuses; EPUB never got it). Nothing persisted *which* chunk had failed: every EPUB checkpoint row was
stamped `completed` and `progress.failed_chunks` excluded `fallback_used`. And the per-file XHTML partial
states were saved under `chapter1.xhtml` but deleted under `OEBPS/chapter1.xhtml`, so the delete never
matched and every stale state declared its file finished at chunk N/N. Measured on the repro: pass 1 made
6 chunk-level LLM calls, the resume made 0, and even rewinding the file pointer to 0 made 0.

**Shipped.** Six phases, per `plan/PLAN_Issue261_RetryFailedChunks.md`. One shared helper computes a
partial state's path so save/load/delete/list can never disagree again, and the file loop deletes with
the authoritative `content_href`. The partial state now carries a per-chunk status (`pending`,
`translated`, `token_aligned`, `untranslated`; `token_aligned` is never retried — those chunks are
translated, only their placeholder positions were approximated) and is *kept* while the file still has
unfinished chunks instead of always being deleted. The job progress carries
`epub_unfinished_units: {file_href: [chunk_index, ...]}`, the complete current picture of what is owed,
rewritten whole and never merged. Resume *is* the retry: the file loop re-enters a file below the resume
pointer when it has both a ticket in that map and a loadable partial state, retranslates exactly its
unfinished chunks, and never rewinds the pointer itself. And the verdict stopped depending on history —
`classify_completion` now bases `partial` on `unfinished_chunks` (work still owed) and falls back to the
`fallback_used` tally only when that key is absent, so a job that retried every fallen-back chunk
successfully can finally reach `completed`. The partial card and the resumable-job badge name the count
and the files holding it, in all seven locales.

**Verified by.** `tests/test_epub_retry_failed_chunks.py` (no network, under a second, the CI-side gate)
plus the two-mode real-Ollama acceptance harness
`tests/standalone/repro_issue_261_failed_chunks_unrecoverable.py`: `--mode heal` (resume with the
starvation lifted → the chunk is retried exactly once, the chapter comes out translated, verdict
`completed`, ticket and partial state gone, checkpoint cleaned up) and `--mode persist` (starvation kept
→ retried exactly once, still `partial`, ticket and state kept, output unchanged — no false success, no
retry loop). Both exit 0; before the fix `--mode heal` failed with `retried=0` and the chapter still in
the source language.

**Known follow-up.** Plain Text Mode retry is out of scope (§5 of the plan). Its per-paragraph failures
fall back to source text inside `src/core/common/plain_text_pipeline.py` and never touch `fallback_used`,
so they are invisible to both the old and the new verdict. Its checkpoint key was fixed here, its retry
semantics were not: applying the same per-chunk status treatment to `translate_paragraphs_plain` is the
follow-up.

**Effort.** L (shipped). **Unblocks.** Item 5.5.

---

### [ ] 1.3 — Issue #241: Docker version check reports an update that is already installed

**Symptom.** Two users, one on Docker and one on Linux, see the update banner claiming a new version is
available immediately after updating.

**Verified cause.** `git show v1.4.10:src/__version__.py` returns `__version__ = "1.4.9"`. The tag was
cut without bumping the version file, so the running app compares its own stale string against the
GitHub release tag and always finds itself behind. Diagnosed correctly by `aronsky` in the thread.

**Fix direction.** Bump `src/__version__.py`, then prevent recurrence: add a release-workflow step that
fails if the pushed tag does not match `src.__version__`. This is the actual value of the item, the
version string itself is a one-line fix that will be forgotten again otherwise.

**Done when.** `src/__version__.py` matches the latest tag, and a CI job rejects a mismatched tag.

**Effort.** S.

---

### [ ] 1.4 — Issue #208: TXT output loses paragraph breaks at every chunk boundary

**Symptom.** In non-bilingual TXT mode, paragraph separations disappear at each chunk seam. The chunker
splits on `\n\n` (`src/core/chunking/token_chunker.py:59`), but reassembly does not restore it.

**Verified cause.** Two joiners:
- `src/core/adapters/txt_adapter.py:151` — `joiner = "\n\n" if bilingual else "\n"`
- `src/core/refine/txt_refiner.py:129` — `final_text = "\n".join(refined_parts)`

**Fix direction.** Join with `"\n\n"` in both paths. Verify no double-blank-line regression when a chunk
already ends with a newline; strip trailing whitespace per chunk before joining.

**Done when.** A multi-chunk TXT translation reproduces the source paragraph structure at every seam, in
both the translate and refine paths. Covered by a test with a three-paragraph, two-chunk input.

**Effort.** S.

---

### [ ] 1.5 — Issue #225: a late or foreign `completed` event resets the UI mid-batch

**Symptom.** During a batch, the whole UI resets to idle and the queue display is lost, while
translation continues server-side.

**Verified cause.** `src/web/static/js/translation/translation-tracker.js:397-406`. When
`currentJob` is null, the only guard before calling `resetUIToIdle()` is that `data.translation_id`
exists and the status is terminal. There is no check that the job belongs to this tab.
`finishCurrentFileTranslation` (`:585`) sets `currentJob` to null *before* advancing the queue
(`processNextFileInQueue()` at `:588`/`:596`), so any straggler event arriving in that window wipes the
batch UI.

**Fix direction.** Track the set of translation IDs this tab started and ignore terminal events for
unknown IDs. Alternatively, do not null `currentJob` until the next file's job is registered.

**Done when.** A synthetic terminal event with an unknown `translation_id` no longer resets the UI, and a
batch of three files completes without visual reset between files.

**Effort.** S.

---

### [ ] 1.6 — Issue #224: desync recovery dispatches events nobody listens to

**Symptom.** After a WebSocket drop, the UI stays stuck on "in progress" until a manual page reload,
even though the recovery code believes it resynced.

**Verified cause.** `src/web/static/js/utils/lifecycle-manager.js:228` dispatches a window
`CustomEvent('translationUpdate')` and `:240` dispatches `CustomEvent('resetUIToIdle')`. A repo-wide grep
over `src/web/static/js` for `addEventListener('translationUpdate'` and `addEventListener('resetUIToIdle'`
returns zero matches. The real Socket.IO handler is bound to `translation_update` (underscore). The
recovery path is a silent no-op.

**Fix direction.** Call the tracker's handlers directly (`TranslationTracker.handleTranslationUpdate`,
`resetUIToIdle`) instead of dispatching dead events, or register listeners for the two event names. Prefer
the direct call and delete the dead dispatch, one less indirection.

**Done when.** Killing the WebSocket mid-job and letting it reconnect resyncs the UI without a reload.

**Effort.** S. **Related.** Item 1.5, same file cluster, ship together.

---

### [ ] 1.7 — Close five stale issues

All verified as already resolved in code. Closing them removes a third of the open-issue noise and makes
future triage cheaper.

- **#183** — switching model/provider when resuming. Shipped v1.4.1. `POST /api/resume/<id>` accepts
  `model`, `llm_provider`, `llm_api_endpoint`, `api_key`, `context_window` overrides
  (`src/api/blueprints/translation_routes.py:318-319`).
- **#167** — notification system. Shipped v1.3.3. `src/utils/notifier.py`, `NOTIFY_WEBHOOK_URL` config,
  `tests/unit/test_notifier.py` all present.
- **#155** — EPUB upload failure and stuck LLM indicator. Two causes fixed: init race (`c5ec9b4`) and
  Windows registry serving `.js` as `text/plain` (`f90532a`, v1.2.2). The `mimetypes.add_type` calls are
  present at `translation_api.py:12-16`.
- **#174** — context length question. Answered as an Ollama/LM Studio configuration matter
  (`MAX_TOKENS_PER_CHUNK`, `OLLAMA_NUM_CTX`). No code gap. Consider linking the answer into
  `docs/TROUBLESHOOTING.md` before closing.
- **#229** — stats counters. Fixed by a later refactor:
  `src/core/adapters/generic_translator.py:280-299` now accumulates `failed_count` via `nonlocal`, and
  the success path at `:358-373` uses a separate `completed_count` incremented only on genuine save
  success. No trace of the reported behaviour remains.

**Also.** Consolidate #140 and #234 into a single PDF tracking issue (see item 5.1), and close #196 with
a pointer to #198, which already tracks its only in-scope part.

**Effort.** S, no code.

---

## Sprint 2 — security and data integrity

Goal: close the two real security primitives and stop the two silent data-corruption paths. PR #233
(CORS lockdown plus per-session token on every `/api/` route) changed the *reachability* of several of
these but not the underlying flaws, which is why they remain here.

### [x] 2.1 — Issue #215 (PR #252): zip-slip on EPUB resume reconstruction

**Symptom.** A crafted EPUB containing `../` entry paths can write files outside the intended directory
when a job is resumed.

**Verified cause.** `src/utils/security.py` `_validate_epub_file` (around `:380-430`) checks mimetype,
zip-bomb ratios and suspicious extensions, but never validates entry paths.
`src/persistence/checkpoint_manager.py:678` calls `zip_ref.extractall(temp_path)` unguarded, and `:744`
joins `job_dir / file_href` with no containment check. Not touched by PR #233, which addressed an
unrelated code path.

**Fix direction.** Validate every archive entry before extraction: reject absolute paths, drive letters,
and any entry whose resolved path is not inside the destination. Do the same for the `file_href` join.
Prefer a shared helper in `src/utils/security.py` used by both call sites, and reject at validation time
so a malicious EPUB never reaches extraction.

**Done when.** A test fixture EPUB containing `../../evil.txt` is rejected at validation, and a second
test asserts nothing was written outside the temp directory.

**Effort.** M. **Severity.** Arbitrary file write.

---

### [x] 2.2 — Issue #211 (PR #252): SSRF and API-key exfiltration via `llm_api_endpoint`

**Symptom.** A request can point the translation job at an attacker-controlled endpoint while the server
supplies its own stored API key, sending that key to the attacker.

**Verified cause.** `src/api/blueprints/translation_routes.py:177` accepts `llm_api_endpoint` verbatim
from the request body. No allowlist exists anywhere in the repo. `resolve_api_key()` in
`src/api/api_keys.py` falls back to the server's `.env` key whenever the request sends an empty value or
the `__USE_ENV__` sentinel, so the override does not have to carry its own credential.

**What PR #233 changed.** It removed wildcard CORS and gated `/api/` behind a per-session token, which
closes the drive-by cross-site delivery described in the original report. The flaw itself is unpatched:
it remains reachable by anyone able to present the session token, that is via XSS, a malicious browser
extension, or a token shared on a LAN.

**Fix direction.** Never pair a request-supplied endpoint with a server-stored key. Two guards, both
cheap:
1. Allowlist endpoint hosts (known provider domains plus loopback and private ranges for self-hosted
   Ollama and LM Studio, ideally configurable via `.env`).
2. If the request overrides the endpoint, require it to also supply its own key, and refuse the `.env`
   fallback in that case.

**Done when.** A job pointing at an unlisted external host is rejected, and a job overriding the endpoint
without a key does not fall back to the stored credential. Both covered by tests.

**Effort.** S. **Severity.** Credential exfiltration.

---

### [x] 2.3 — Issue #206 (PR #252): DOCX rebuild drops blockquotes and duplicates nested content

**Symptom.** Translated DOCX output silently loses blockquote content and repeats nested list items. Mixed
header/data table rows come out reordered.

**Verified cause.** All in `src/core/docx/converter.py`:
- `:374-388` `_convert_html_element_to_docx` branches only on `p`, `h1`-`h6`, `ul`/`ol`, `table`, `br`,
  `img`. The comment at `:388` reads `# Skip other tags`, so `blockquote` falls through and is dropped.
- `:421` `_convert_list` uses `element.findall('.//li')`, a descendant search, while `_get_text_content`
  (`:531-533`) uses `itertext()` which already recurses. A nested `<li>` is therefore emitted once inside
  its parent's text and again on its own.
- `:432` `_convert_table` has the identical `.//tr` problem for nested tables, and `:449` builds cells as
  `findall('.//td') + findall('.//th')`, putting all data cells before all header cells regardless of
  source order.

**Fix direction.** Add a `blockquote` branch mapping to the Word "Quote" style (or an indented paragraph
if the style is absent). Replace the descendant searches with direct-child iteration for both `li` and
`tr`, and recurse explicitly for nesting. Build the cell list in document order rather than by tag.

**Done when.** A round-trip test on a DOCX containing a blockquote, a two-level nested list, a nested
table and a mixed `th`/`td` row reproduces the structure faithfully.

**Effort.** M. **Severity.** Silent content corruption in a shipped format.

---

### [x] 2.4 — Issue #228 (PR #252): a rate-limit pause discards work already paid for

**Symptom.** When a provider rate-limits a run and the app auto-pauses, refine-mode and plain-text-mode
work already completed in that pass is thrown away. Resuming re-sends and re-pays for the same chunks.

**Verified cause.**
- `src/core/translator.py` `refine_chunks` (around `:826-835`) fills a local `refined_parts` list with
  remaining chunks on `RateLimitError`, then re-raises bare, discarding the list without a checkpoint.
- `src/core/common/plain_text_pipeline.py:268-271` calls `_fill_remaining_with_source()` and then
  `:328-329` raises `rate_limit_error`, same shape.

The interruption path immediately below (`:333-341`) does the right thing and returns the reassembled
partial result. Only the rate-limit path loses it.

**Fix direction.** Make the rate-limit path mirror the interruption path: persist the partial result and
checkpoint before propagating the pause. Rate-limit pauses are routine, not an edge case, so this is
recurring waste rather than a rare loss.

**Done when.** A run interrupted by a simulated 429 mid-pass resumes without re-translating chunks that
had already succeeded.

**Effort.** M.

---

### [x] 2.5 — Issue #216 (PR #252): `PRAGMA foreign_keys` is never enabled, so checkpoints never cascade-delete

**Symptom.** The database grows without bound. `checkpoint_chunks` rows, which hold full original and
translated text, are never removed.

**Verified cause.** `src/persistence/database.py` `_get_connection` (`:53-65`) sets only
`journal_mode=WAL` (`:62`) and `busy_timeout` (`:63`). The schema at `:106-109` declares
`ON DELETE CASCADE`, which SQLite enforces per connection only when the `foreign_keys` pragma is on.
`delete_job` (`:594-618`) and `cleanup_old_jobs` (`:479-521`) delete only from `translation_jobs` and
rely on a cascade that never fires. The comment at `:509` asserting "chunks deleted via CASCADE" is
wrong.

**Fix direction.** Add `conn.execute("PRAGMA foreign_keys = ON")` in `_get_connection`. Add an explicit
`DELETE FROM checkpoint_chunks` in both methods as defence in depth, and ship a one-off cleanup for
already-orphaned rows.

**Done when.** Deleting a job removes its chunk rows, and a migration reclaims existing orphans.

**Effort.** S.

---

## Sprint 3 — provider layer and config plumbing

Goal: a batch of independent small fixes in the LLM layer. Individually low severity, collectively the
reason cost reporting, key handling and error messages cannot be trusted.

### [ ] 3.1 — Issue #230: per-job NIM API key is silently dropped for TXT, SRT and DOCX

`nim_api_key` is threaded through the EPUB path only. The TXT and SRT `llm_config` dict and the DOCX
`create_llm_provider` call in `src/core/adapters/translate_file.py` omit it, so
`src/core/llm/factory.py:163` falls back to `os.getenv("NIM_API_KEY")`. A user supplying a per-job key in
the UI silently gets the server's key, or none at all.

**Done when.** All four formats forward the per-job key. Add a test asserting the factory receives it.
**Effort.** S.

---

### [ ] 3.2 — Issue #218: OpenRouter and Poe cost tracking is shared across jobs and reads a missing field

Two defects:
- `src/core/llm/providers/openrouter.py:62-64` and `poe.py:76-78` declare `_session_cost`,
  `_session_tokens` and `_cost_callback` as **class** attributes, mutated via classmethods called from
  `src/api/handlers.py:225-227` and `:679`. Concurrent jobs, which the app supports, contaminate each
  other's totals.
- `openrouter.py:281` checks for a top-level `"cost"` key, but the request payload (`:231-237`) never
  sets `"usage": {"include": true}`, so the field is never present and the code always falls through to
  the hardcoded $0.50/$1.50-per-million estimate at `:285`.

**Done when.** Cost state is per-instance, and OpenRouter requests ask for usage so real costs are used
when available, with the estimate kept as an explicit fallback.
**Effort.** S.

---

### [ ] 3.3 — Issue #219: an invalid Mistral or DeepSeek key is retried instead of failing fast

In `mistral.py`, `raise ValueError("Invalid Mistral API key")` on `status_code == 401` sits inside the
same `try` whose trailing `except Exception` (around `:329`) catches it, logs "API Unknown Error", sleeps
and retries, eventually returning `None`. `deepseek.py` has the identical shape. As a consequence the
401 branches inside the `HTTPStatusError` handler in both files are dead code, since a 401 never reaches
`raise_for_status()`. `poe.py` gets this right and returns `None` immediately, use it as the model.

**Done when.** A bad key surfaces a clear credential error on the first attempt, with no retry storm.
**Effort.** S.

---

### [ ] 3.4 — Issue #220: thinking-model detection is a no-op after the Ollama refactor

`src/core/llm_client.py:139-140` and `:154-158` still probe for `_is_thinking_model` and
`_detect_thinking_model`, but `src/core/llm/providers/ollama.py` now exposes `_detect_thinking_behavior()`
(`:100`) and `_thinking_behavior` (`:44`). The `hasattr` checks fail, so the pre-warm call at
`src/core/translator.py:759-760` does nothing and the repetition-loop mitigation never arms.

Separately, `gemini.py:79` and `deepseek.py:101` define `_is_thinking_model` as a plain method, so
`get_is_thinking_model()` returns a truthy bound method rather than a boolean, and never reports `False`.

**Done when.** Ollama detection runs again, and the Gemini/DeepSeek accessor returns a real boolean.
**Effort.** S/M.

---

### [ ] 3.5 — Issue #231: eight independent correctness bugs in the LLM layer

All eight verified as still present. Good candidate for one grouped PR, one commit each.

1. `pricing_data.py` — the substring-match loop is insertion-order dependent, so a shorter model key can
   shadow a longer one.
2. `ollama.py` streaming — `raise_for_status()` is called inside `client.stream()` before the body is
   read, so `e.response.json()` raises `ResponseNotRead` and is swallowed by a bare `except`.
   Context-overflow detection never fires for streamed 400s.
3. `gemini.py` around `:232` — only `parts[0]` is read, additional parts are dropped.
4. `ollama.py:661` — references `self._context_detector`, never assigned in `__init__`. The
   `AttributeError` is swallowed by a broad `except`.
5. `thinking/detection.py` — `elif phrase_len >= 40` is unreachable after an earlier `phrase_len >= 20`
   check.
6. `litellm.py` — `KeyPool.peek()` is called once before the retry loop, with no `acquire()` or
   `mark_throttled()`. Key rotation is effectively dead for this provider.
7. `thinking/cache.py` — `tested_at` uses `loop.time()`, a monotonic clock, persisted as if it were wall
   clock.
8. `rate_limit_handler.py` — 408 falls in the 4xx non-retryable range, so a request timeout is never
   retried.

**Done when.** Each has a regression test or, where untestable, a comment explaining the invariant.
**Effort.** M for the bundle.

---

### [ ] 3.6 — Issue #226: only the Ollama loader guards against stale provider responses

`src/web/static/js/providers/provider-manager.js` `loadOllamaModels` (`:479-511`) uses a cancellation
token stored in `StateManager` plus a post-await re-check that the selected provider is still Ollama.
None of the other seven loaders do: Gemini (`:608`), OpenAI (`:662`), OpenRouter (`:757`), Mistral
(`:820`), DeepSeek (`:873`), NIM (`:934`), Poe (`:991`). Switching providers while a fetch is in flight
repopulates the dropdown with the previous provider's models.

**Fix direction.** Extract the Ollama guard into a shared helper and wrap all eight loaders.
**Done when.** Rapidly toggling providers never leaves a mismatched model list.
**Effort.** M.

---

## Sprint 4 — i18n and technical debt

Goal: pay down the two i18n violations and delete the dead code, once the correctness work is done.

### [ ] 4.1 — Issue #222: the TTS/audiobook modal is hardcoded English

`showTTSModal` in `src/web/static/js/index.js` (function at `:574`) builds the modal from a template
literal with raw English throughout: `🎧 Generate Audiobook` (`:609`), `TTS Provider` (`:620`),
`GPU Status` (`:631`), `Target Language` (`:643`, `:767`), `Voice (optional)` (`:695`), `Speech Rate`
(`:702`), `Audio Format` (`:714`, `:799`), `Audio Bitrate` (`:723`), `Voice Cloning` (`:737`),
`Exaggeration` (`:750`), `CFG Weight` (`:757`), `Cancel` (`:811`), `Generate Audio` (`:813`), plus every
`<option>` label.

The keys already exist in `src/web/static/locales/en/tts.json` and in all seven locales, they are simply
unwired for the modal body. The adjacent status and progress code (roughly `:100-219`) does use `t()`
correctly, so this is a partial job to finish rather than a greenfield one.

**Fix direction.** Emit `data-i18n` attributes on the injected markup so `applyToDOM` handles reactivity
automatically, per the CLAUDE.md guidance. That avoids adding a `languageChanged` listener for this modal.

**Done when.** Toggling the language selector with the modal open retranslates every label without a page
reload.
**Effort.** M.

---

### [ ] 4.2 — Issue #223: file-queue status strings are raw English and double as logic keys

The queue status is both the display string and the comparison key, so it cannot be translated without
breaking control flow.

- `src/web/static/js/files/file-upload.js:858` sets `status: 'Queued'`, `:1002` renders it directly via
  `statusSpan.textContent = \`(${file.status})\``, and `:934`, `:938`, `:1019`, `:1047`, `:1053` compare
  against the literal.
- `src/web/static/js/translation/batch-controller.js:289`, `:298`, `:308`, `:325`, `:347` set
  `'Preparing...'`, `'Error: Missing API key'`, `'Path Error'`, `'Submitted'`, `'Initiation Error'`.
- `src/web/static/js/translation/translation-tracker.js:321`, `:363`, `:478`, `:573-576` set
  `'Processing'`, `'Completed'`, `'Interrupted'`, `'Rate Limited'`, `'Error'`.
- No `localeChanged` listener exists in `file-upload.js`. Only `files/file-manager.js:36` has one, which
  is why the queue never re-renders on a language switch.

**Fix direction.** Introduce a status-code enum decoupled from display text, update every comparison
site across the three files, add the display keys to all seven locales, and wire a `localeChanged`
re-render for the queue.

**Done when.** The queue shows localized statuses that update live on language switch, and no comparison
anywhere comments against a display string.
**Effort.** L. Largest single i18n item, do not start it mid-sprint.

---

### [ ] 4.3 — Issue #227: delete the dead code and the French docstrings

Verified dead, zero callers repo-wide:
- The error subsystem: `retry_manager.py`, `error_recovery.py`, `error_handler.py`, `error_logger.py`.
  Exported from `adapters/__init__.py` but instantiated nowhere.
- `subtitle_translator.py` — `translate_subtitles` and `translate_subtitles_in_blocks`. The former would
  crash if called: it passes `custom_instructions=` to `generate_translation_request`, which has no such
  parameter.
- `xhtml_translator.py` — `attempt_placeholder_correction`, `build_specific_error_details`,
  `extract_corrected_text`. `src/config.py` defines `MAX_PLACEHOLDER_CORRECTION_ATTEMPTS` twice (around
  `:337` as `2`, then around `:502` as `0`, and the second wins), confirming the path is dead by design.
- `xml_helpers.py` — fully orphaned, not even imported by `epub/__init__.py`.
- `parallel.py` — `iter_ordered_windows` and `gather_window` are used only inside their own module.
  `iter_ordered_concurrent` is the real scheduler, used by `plain_text_pipeline.py`,
  `xhtml_translator.py` and `generic_translator.py`.
- `form-manager.js` — `getTranslationConfig` and `validateConfig` call only each other, while
  `batch-controller.js` carries an actively used, drifted duplicate. Reconcile before deleting.

**Language-policy violations found while verifying**, which CLAUDE.md forbids:
`src/core/common/translation_orchestrator.py` and `src/core/docx/docx_translation_adapter.py` have
French docstrings, and `src/config.py` has French docstrings around `:331` and `:335`.

**Fix direction.** Delete in small reviewable commits, one subsystem each, running the test suite between
them. Translate the docstrings in the same PR. Resolve the duplicated config constant rather than
deleting one occurrence blindly, decide whether placeholder correction should exist at all.

**Done when.** The dead modules are gone, the suite is green, and no French text remains in committed
source.
**Effort.** S/M, mostly deletions.

---

## Feature queue

Ordered by demand signal against effort. None of these should start before Sprint 2 is done.

### [x] 5.1 — PDF support (#140, #234, discussion #191)

Highest cumulative demand: three distinct reporters across two duplicate issues plus a discussion. No PDF
handling exists anywhere in `src/`.

The pragmatic scope you already proposed in both threads, text-only PDF converted to Markdown or plain
text, was agreed to by the reporters and never built. A third user (`a-bashtannik`) contributed
implementation notes: PyMuPDF plus marker-pdf plus Pillow, with hyphenation, footnotes and page-boundary
reassembly flagged as the genuinely hard parts.

**First action.** Merge #140 and #234 into one tracking issue and write the agreed scope into it, so the
"will you support PDF" question stops being answered three times. Note that discussion #191 currently
answers it as "pre-convert with pdf-craft", which stays a valid interim answer.
**Effort.** L.

**Shipped.** Text PDFs are accepted as input and produce a PDF, per
`blueprint/BLUEPRINT_PdfSupport.md`. `src/core/pdf/extractor.py` reads the text layer with PyMuPDF
(the only new dependency) and reassembles paragraphs across lines, blocks and pages: hyphenation is
undone, running headers, footers and page numbers are dropped, and headings, list items and images are
kept as coarse styles. The paragraphs go through the existing plain-text pipeline
(`translate_paragraphs_plain`) and its segment checkpoint, reusing the DOCX Plain Text Mode contract
with no schema change, so an interrupted job resumes at the first unattempted segment.
`src/core/pdf/builder.py` then writes a new, reflowed PDF on pages of the original size; the original
layout is not preserved. A follow-up (track A) also rebuilds ruled tables as tables and keeps paragraph colours,
background boxes and the font family (serif, sans-serif, monospace), per `blueprint/BLUEPRINT_PdfFormattingA.md`.
`src/core/pdf/translator.py::translate_pdf_file` is routed from
`translate_file()` for both the web app and the CLI. Out of scope: scanned PDFs (no OCR, they are
rejected with a clear error), faithful multi-column layouts (best effort), and refinement
(`--refine` is ignored with a warning, `--refine-only` and the web refine-after pass are skipped for PDF).

---

### [x] 5.2 — Issue #250: gender support in the glossary

When the source language does not mark gender, such as Chinese, the model defaults every character to
"he", corrupting character identity across a whole book.

**Shipped.** An optional `gender` column (`male` / `female` / unset) on glossary entries, auto-detected by
the NER pass and injected into the prompt.

The load-bearing design decision: the gender is carried by a **cast block that is not chunk-filtered**
(`build_cast_block` in `src/core/glossary/injector.py`). Filtering it like the rest of the glossary would
have missed the reported bug entirely — the chunk where the gender is needed is the chunk where the
character's name is *absent*, which is why the model guesses there. Cost is ~10 tokens per gendered entry
per chunk, capped at 80 entries via `GlossaryConfig.max_cast_entries`.

Two deliberate restraints, both to avoid shipping confident errors: unrecognized gender values (including
`unknown`) normalize to NULL rather than being stored, and the NER pass is instructed to answer `unknown`
whenever the sample carries no gender evidence, with those rows flagged amber in the review table. A wrong
gender ships silently; a blank gets reviewed.

Non-binary and ungendered characters are intentionally out of scope for the column — the documented
guidance is to leave the gender unset and handle them via style instructions.

---

### [ ] 5.3 — Discussion #248: use a different model for the refine pass

The chained refine pass already exists (`src/api/blueprints/translation_routes.py:196-197`,
`src/api/handlers.py:473-511`) but hardcodes `llm_provider=config.get('llm_provider', 'ollama')` and
`model_name=config['model']`, reusing the translation model. The ask is "translate cheap, refine strong",
which the architecture already almost supports.

**Fix direction.** Accept optional `refine_provider` / `refine_model` / `refine_api_key` in the job
config and thread them into the refine call. Related to #183's resume-override plumbing, which solved the
same class of problem.
**Effort.** M. **Action.** Convert the discussion into a tracked issue first.

---

### [ ] 5.4 — Discussion #249: expose chunk size in the CLI and the UI

`MIN_CHUNK_SIZE`, `MAX_CHUNK_SIZE` and `MAX_TOKENS_PER_CHUNK` already exist as `.env`-only settings
(`src/config.py:317-318`, `:341`, `:655-659`). There is no `translate.py` flag and no UI field, so the
knob is undiscoverable. Cheap win, and it also answers the recurring class of question behind #174.
**Effort.** S.

---

### [ ] 5.5 — Issue #232: optional per-file failure log

A power user asked for an optional `.txt` export summarizing which chapters fell back or failed, in a
`Cap_1.html = ...` form, to review quickly in Calibre. Nothing like it exists.

The dependency on item 1.1 is satisfied, and the data this export needs now exists: item 1.8 persists a
per-chunk status in each file's XHTML partial state and indexes the remaining work per file in
`progress['epub_unfinished_units']` (`{file_href: [chunk_index, ...]}`), which is exactly the
`Cap_1.html = ...` shape the reporter asked for. What is left is rendering it to a `.txt` next to the
output. Caveat: that index covers the EPUB path only — plain-text mode still has no per-paragraph status
(the follow-up flagged in item 1.8), so a plain-text run would produce an empty log.
**Effort.** S/M. **Depends on.** Item 1.1 (done), item 1.8 (done).

---

### [ ] 5.6 — Issue #242: HTML `<ruby>` elements need dedicated handling

Zero occurrences of "ruby" anywhere in `src/`. Ruby annotations in Japanese and Korean text currently
reach the model unhandled and confuse it, especially in plain-text mode. The reporter submitted a
well-formed proposal including a fallback to the `<rp>` parenthesis form.
**Effort.** M/L.

---

### [ ] 5.7 — Issue #243: Markdown file support

`FileType` in `src/utils/file_detector.py:18` is `Literal["txt","epub","srt","docx"]`. A `.md` file is
sniffed and silently handled as plain text (`:22`), so headers, lists and code fences are not protected
from the model.
**Effort.** M.

---

### [ ] 5.8 — Issue #198: hydrate active jobs from the server on page load

A second browser sees nothing for a job currently running elsewhere.
`src/web/static/js/translation/translation-tracker.js:159-160` restores active jobs only from
localStorage, and `src/persistence/database.py:438-459` `get_resumable_jobs()` filters
`status IN ('paused','interrupted','error','partial')`, deliberately excluding `running`. This is the
one in-scope piece salvaged from the #196 audit.
**Effort.** M.

---

### [ ] 5.9 — Discussion #238: dynamically maintained relationship and glossary context

The most substantive unimplemented feature in the backlog. The current glossary is a static user-curated
term list; this asks for an evolving per-chunk context capturing character genders, forms of address and
relationships, maintained by an extra LLM call.

A community member (`windfox1243`) has a working prototype fork and answered your design questions in the
thread. The known costs: one extra LLM call per chunk, and translation becomes sequential because the
state evolves, which removes parallelism.

**Action.** Convert to a tracked issue capturing the design questions already raised: context storage
shape, prompt placement, growth control, and the cost/parallelism tradeoff.

**What item 5.2 already settled.** Gender was the narrow version of this problem and shipped first, which
answers two of the four design questions cheaply. Prompt placement: a dedicated block ahead of the
glossary block, injected unconditionally. Growth control: a hard entry cap with a once-per-job warning
rather than unbounded growth. It also showed that a *static* per-book context needs no extra LLM call and
no sequential execution, so the remaining open question is narrower than the discussion assumes — what
genuinely requires per-chunk evolution, rather than a one-shot extraction pass reused everywhere.
**Effort.** L.

---

### [ ] 5.10 — Discussion #146: cross-provider key-pool cascading

Key rotation on 429 shipped (comma-separated `*_API_KEY`, `docs/API_KEY_ROTATION.md`). The unaddressed
half is the cascade suggested by `virdb`: fall back Gemini to OpenAI to Ollama when a whole provider is
exhausted. Split it into its own issue so the shipped part stops being confused with the pending part.
**Effort.** M.

---

### [ ] 5.11 — PDF layout-preserving mode (track B)

Item 5.1 rebuilds a new, reflowed PDF, and track A improved its fidelity with tables, colours, boxes and
font families, but the page layout is still regenerated rather than kept. Track B would translate the text
in place on the original pages, keeping positions, columns, graphics and fonts. It needs text-fitting rules
for translations that are longer or shorter than the source, and a policy for text that does not fit. It is
a different rendering strategy rather than an extension of the reflow builder, and out of scope of track A.
**Effort.** L.

---

## Pull requests

### [ ] PR #245 — Portuguese (Brazil) and Portuguese (Portugal) distinction

+101/-18 across 12 files. Adds `src/utils/lang_normalize.py`, updates `lang_support.py` for BCP47,
`tts_config.py`, `pricing/estimator.py`, `context_optimizer.py`, prompt helpers, six HTML dropdowns and
browser-locale detection in `form-manager.js`. Ships a test
(`test_epub_xhtml_lang_attributes.py`). Language dropdown options are plain text with no `data-i18n`
already on `main`, so this introduces no new i18n violation.

**Verdict.** Merge, optionally with a nit: `src/tts/providers/chatterbox_tts.py` and
`src/utils/language_detector.py` still map `"pt"` to `"Portuguese"` for source detection. Low impact.
**Order.** Merge first, smallest risk of the three.

---

### [ ] PR #247 — Atlas Cloud provider

+336/-8 across 34 files. Best engineering quality of the three: follows the full provider registration
pattern (factory, config, api_keys, adapters, refiners, frontend wiring) and adds three test files
including `test_atlascloud_provider.py`.

**Blocking before merge:**
- Locale keys added only to `en` (`settings.json`, `errors.json`). The six other locales are missing every
  `atlascloud_*` and `api_key_required_atlascloud` key, which violates the CLAUDE.md rule.
- Not wired into `translate.py`: absent from the `--provider` choices and no `--atlascloud_api_key`.
- Absent from `README.md`: no badge, no row in the API-key table.

**Verdict.** Request changes, then merge. All three gaps are mechanical.

---

### [ ] PR #244 — Requesty provider

+214/-14 across 11 files, entirely inside `benchmark/` plus docs and `.env.example`. Zero changes under
`src/`. Requesty is therefore not selectable in the web UI or in `translate.py`, despite the PR title.

**Verdict.** Ask the author which was intended. If benchmark tooling only, retitle to
"Add Requesty to the benchmark CLI" and merge, it is low risk and self-contained. If a real provider was
intended, it needs the full registration pattern that PR #247 demonstrates.

**Note on CI.** None of the three PRs has any recorded review or status check. Worth confirming whether
fork PRs are meant to run CI at all, since that gap will keep costing review time.

---

## Housekeeping

Already fixed in code, no action beyond closing. Full evidence in item 1.7.

| Issue | Resolution |
| --- | --- |
| #183 | Shipped v1.4.1 |
| #167 | Shipped v1.3.3 |
| #155 | Shipped v1.2.2 |
| #174 | Answered, config matter |
| #229 | Fixed by a later refactor |
| #196 | Superseded by #198 |
| #140 / #234 | Merge into one PDF issue |

Discussions already resolved and worth closing or marking answered: #173 (auto-update, shipped v1.3.7),
#199 (bilingual output, fixed v1.4.9), #182 (LM Studio already works through the generic OpenAI-compatible
provider, a discoverability issue rather than a code gap, worth a line in `docs/PROVIDERS.md`), #176
(fixed differently in v1.3.12), #169 (fixed v1.3.13 and v1.3.14), #164 (shipped v1.2.3), #165 (version now
in the header), #161 (shipped as the glossary), #126 (backoff and auto-resume shipped v1.0.20/v1.0.21).

---

## Deferred and product decisions

### Issue #221 — device fingerprint sent to LLM providers

Resolved by removal. The project used to derive a per-install identifier from the machine's MAC
address and:

- attach it as an `X-Session-Token` header on every LLM request;
- embed it as zero-width characters inside translated TXT/SRT output;
- write it into the EPUB `dc:identifier` and the DOCX `last_modified_by` metadata field;
- append it to DEBUG log lines.

None of this was disclosed to users, and there was no opt-out in `src/config.py`. Rather than
disclose-and-opt-out, the decision was to remove the fingerprint entirely: the identifier, the
headers, the embedding, and the metadata writes are gone.

The existing `ATTRIBUTION_ENABLED` / `GENERATOR_NAME` / `GENERATOR_SOURCE` attribution is kept: it
is visible in the output, identical across every install (it does not identify a specific user or
machine), and can be switched off. A separate, lawful, opt-in usage-statistics mechanism is planned
to replace the removed telemetry; see `plan/PLAN_UsageStatistics.md`.

### Issue #212 — self-update endpoint hardening

The primary claim, an unauthenticated CSRF-reachable update endpoint, is closed: `/api/version/update` is
not in the `_EXEMPT_ENDPOINTS` frozenset in `src/api/auth.py`, so PR #233's token gate covers it.

What remains is lower-priority supply-chain hardening: `requirements.txt` is fully unpinned, there is no
lockfile, and no commit or signature verification runs before `git pull` plus `pip install --upgrade`.
Keep the issue open but retitle it to reflect the remaining scope, or split it out and close the original.

### Issue #196 — deep repository and UI audit

The multi-user and SaaS framing was rejected by design, the project is intentionally single-user and
self-hosted. The one in-scope item became #198 (item 5.8). Close with a pointer.

---

## Cross-cutting notes

**Item 1.1 unlocked three other items, and is done.** Correctly separating "failed" from "fell back to
source" was the prerequisite for the #246 diagnosis (item 1.2, shipped with it), for making those chunks
retryable (item 1.8, shipped) and for the failure-log export (item 5.5, now only a rendering job over
data that already exists). The one gap left in that chain is plain-text mode, which still has no
per-paragraph status — see the follow-up in item 1.8.

**PR #233 changed reachability, not correctness.** Items 2.2, and the deferred #212 and #214, all had
their delivery vector closed by the CORS and session-token work while the underlying flaw stayed. When
re-reading those issues, do not take the closed vector as a fix.

**Three frontend items share one cluster.** Items 1.5, 1.6 and 3.6 all live in the tracker and lifecycle
code. Shipping them together is cheaper than three separate reviews, but keep 4.2 out of that batch, it
is a different order of magnitude.

**Recurring class: state exists but is not exposed.** Items 5.4 (chunk size), 5.3 (refine model) and 5.10
(key cascade) are all cases where the capability is already in the code and only the surface is missing.
They are the cheapest user-visible wins in the feature queue.

**Every new user-facing string** must land in all seven locales (`en`, `fr`, `es`, `de`, `zh-CN`, `ja`,
`ko`) in the same commit, and must re-render on language switch. This applies to items 4.1, 4.2, 5.2 and
PR #247.
