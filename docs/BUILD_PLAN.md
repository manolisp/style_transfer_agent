# BUILD_PLAN.md — implementation plan for Antigravity

Build in this order. Each task has **acceptance criteria** the agent should verify (it has a
terminal) before moving on. Prefer **Planning Mode** and approve the plan first.

## Kickoff prompt (paste into the Agent Manager)

> Build the Style-Match Photo Editor Agent described in `docs/SPEC.md`, using the verbatim
> prompts in `docs/PROMPTS.md` and following the rules in `AGENTS.md`. Work task by task
> through `docs/BUILD_PLAN.md`. After each task, run its acceptance check and show me the
> result before continuing. Create two files: `agent.py` (logic, no UI) and `app.py`
> (Gradio v6 UI importing agent). Do not send the reference image to the image model; the
> edit must be a content-locked tonal re-grade; the critic must compare against the original.
> Use `uv`/`pip` per `docs/SETUP.md`. Start in Planning Mode and show me the plan first.

---

## Task 0 — Environment
- Create the project, install `google-genai`, `pillow`, `gradio` (see `SETUP.md`).
- **Accept:** `python -c "import google.genai, PIL, gradio"` succeeds.

## Task 1 — `agent.py` skeleton + prompts + helpers
- Add module constants `IMAGE_MODEL`, `THINK_MODEL`; a **lazy** client accessor; the four
  prompt constants **copied verbatim** from `PROMPTS.md`; `DETERMINISTIC_KEYWORDS`.
- Implement `parse_json` (defensive), `load_image`.
- **Accept:** module imports **without** `GEMINI_API_KEY` set; `parse_json` handles a
  ```json-fenced string and a `{...}` embedded in prose.

## Task 2 — Perceive, route, deterministic edit, prompt builder
- `perceive(reference, instruction)` → StyleSpec via THINK_MODEL.
- `plan(spec, instruction, has_reference)` → "deterministic" | "generative" (§4.5 of SPEC).
- `deterministic_edit(img, spec)` (Pillow, §4.6).
- `build_edit_prompt(spec, has_reference=False)` fills `EDIT_TEMPLATE` (content lock is
  unconditional; `has_reference` kept only for call-site compatibility).
- **Accept:** `build_edit_prompt` output contains "COLORIST" and "content must NOT change";
  `plan` routes "make it black and white" (no ref) → deterministic, a style phrase →
  generative, any reference → generative; `deterministic_edit` returns a same-size RGB image.

## Task 3 — Generative edit (source only) with robust error handling
- `generative_edit(source, reference, prompt)` — **ignore `reference`**; send
  `contents=[prompt, source]`. Parse `resp.candidates[0].content.parts` for `inline_data`.
- Handle: prompt blocked (`resp.prompt_feedback.block_reason`); no candidates; candidate with
  `content is None` (safety block) → raise a clear `RuntimeError` including `finish_reason`;
  text-only response → error hinting `response_modalities=['TEXT','IMAGE']`.
- **Accept:** with a mocked client, a candidate whose `content=None` raises a message
  containing the finish reason (no `AttributeError`); a normal inline-image response returns a
  PIL image; a reference passed in is **never** placed in `contents`.

## Task 4 — Critique (vs original) + refine + the loop
- `critique(edited, spec, source)` → Verdict; sends ORIGINAL + EDITED + spec (§4.3).
- `refine_prompt(prev, verdict, spec)` → new edit-prompt text.
- `run(source, reference, instruction, max_iters, threshold, on_iter)` per §4.4 pseudocode:
  perceive → route → (deterministic short-circuit) → loop edit/critique/refine; call
  `on_iter(i, image)` each iteration; stop at threshold.
- **Accept (mocked loop):** reference never reaches the image model; a verdict with
  `content_preserved=false` (score ≤4) triggers a refine and a second attempt; a subsequent
  score ≥ threshold stops the loop; deterministic route returns immediately with empty history.

## Task 5 — `agent.py --selftest`
- Add a `selftest()` and `main()` CLI (`argparse`) with `--selftest`, plus flags to run a
  single edit from the command line (`source`, `--reference`, `--instruction`, `--out`,
  `--max-iters`, `--threshold`, `--no-human`, `--save-iters`).
- **Accept:** `python agent.py --selftest` prints PASS for parse_json, build_edit_prompt,
  deterministic_edit, and plan/router, with no API calls.

## Task 6 — `app.py` Gradio UI (v6)
- Import `agent`. Build the layout in `SPEC.md` §2.5. State via `gr.State` only.
- `do_run(...)` runs the loop, collecting iterations via `on_iter`; returns result, gallery,
  spec, trace, and state (source, reference, spec, history, last_prompt, **result, iters**).
- `apply_feedback(...)` refines once from the typed feedback and regenerates; updates result,
  trace, history, prompt, and the result state.
- Error-surfacing wrapper around callbacks; `demo.launch(show_error=True)`.
- **Accept:** app launches; `do_run`/`apply_feedback` return the expected number of values
  (mock the agent to test wiring headless); errors show a real message in the toast.

## Task 7 — Downloads (full-res JPG + ZIP)
- `_save_jpg(img, name)` → JPEG quality 95, subsampling 0, no resize.
- "Download result (JPG)" button (DownloadButton) builds the file on click from result state.
- "Download all iterations (ZIP)" bundles `iter_0.jpg … final.jpg`.
- Gallery: `allow_preview=True`, `object_fit="contain"`; **do not** pass
  `show_fullscreen_button`/`show_download_button` (removed in Gradio 6).
- **Accept:** downloaded JPG opens as JPEG at the model's full resolution; ZIP contains all
  iterations + final; clicking a download before any run shows a friendly error, not a crash.

## Task 8 — End-to-end verification (needs key + billing)
- Set `GEMINI_API_KEY`, confirm image generation is enabled (billed Tier-1). Run a real
  source+reference edit.
- **Accept:** result keeps the same composition/objects as the source, re-graded to the
  target tone; the critique trace shows scores; a feedback refinement works; downloads produce
  full-res JPGs. Confirm a deliberately weak first result improves on iteration 2.

---

## Reference: mocked-loop test (Task 4 acceptance, no API calls)

The agent should write a test equivalent to this to prove the loop logic:
- Monkeypatch the lazy client with a fake whose `generate_content` returns:
  a StyleSpec for `PERCEPTION_PROMPT`; a real inline PNG for `IMAGE_MODEL` (recording the
  `contents` it received); a verdict with score 3 + `content_preserved=false` on the first
  image call, then score 9 on the second; and a refined prompt for `REFINE_PROMPT`.
- Assert: (a) no captured `contents` list contains the reference image and each contains
  exactly the source; (b) history scores are `[3, 9]`; (c) a refined prompt appears in history;
  (d) deterministic route yields empty history and never calls `IMAGE_MODEL`.

## Optional stretch (Day-5 flavour)
- Dockerfile + `requirements.txt` and `demo.launch(server_name="0.0.0.0", server_port=8080)`
  for Cloud Run.
- An optional "upscale the final result" step or a Nano Banana **Pro** model toggle for
  2K/4K hero output (costs more; keep it off by default).
