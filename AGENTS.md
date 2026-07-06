# AGENTS.md — Style-Match Photo Editor Agent

Always-on instructions for any agent working in this project. Keep this file lean; the full
detail lives in `docs/SPEC.md`, `docs/PROMPTS.md`, and `docs/BUILD_PLAN.md`. Read those
before implementing.

## Project

A Gradio web app wrapping a Python agent that re-grades a photo to match a target *style*
(from a reference image or a text instruction) **without altering scene content**. Agentic
loop: perceive → route → edit → critique → refine, plus a human feedback step.

## Tech stack & conventions

- **Python 3.11+**. Dependency install via `uv` or `pip` (see `docs/SETUP.md`).
- Libraries: `google-genai` (Gemini API), `pillow` (image I/O), `gradio` (UI, **v6.x**).
- Two files: **`agent.py`** (all agent logic, no UI) and **`app.py`** (Gradio UI that
  imports `agent`). Keep the agent importable and UI-free so it can be tested headless.
- LLM prompts live as **module-level string constants** in `agent.py`, copied verbatim from
  `docs/PROMPTS.md`. Prompts are configuration — do not inline-rewrite or "improve" them.
- Type hints on public functions. Docstrings that explain *why*, not just *what*.

## Hard invariants — DO NOT violate or "optimise away"

1. **Never send the style-reference image to the image-generation model.** Passing it makes
   the model copy the reference's *content* (rocks, clouds, water). The editor receives the
   **source photo only**; the target style reaches it purely as text built from the reference.
2. **The edit is a content-locked tonal re-grade, not generation.** The edit prompt must keep
   the colorist framing and all "do not add/remove/move/re-draw, no long-exposure/smoothing,
   pixel-aligned" rules from `docs/PROMPTS.md`.
3. **The critic compares the edit against the ORIGINAL photo** and must cap the score at ≤4 if
   any content changed. The refine loop uses that verdict to correct itself.
4. **Perception describes tone/colour only** — never composition, objects, skies, water
   texture, or long-exposure effects (those are content).
5. **API keys come from the environment** (`GEMINI_API_KEY`), never hardcoded or logged.
6. **Gradio state is in-memory only** (`gr.State`). No `localStorage`/browser storage, no
   HTML `<form>` tags.

## Error handling requirements (these are features, not nice-to-haves)

- Create the Gemini client **lazily** so the module imports (and offline self-test runs)
  without a key.
- Parse model JSON **defensively** (strip ``` fences; fall back to the first `{...}` block).
- In the image call, handle a candidate whose `content` is `None` (a safety block) with a
  clear message including the `finish_reason` — never crash on `.parts`.
- Surface real errors to the UI toast (wrap callbacks; `demo.launch(show_error=True)`), and
  print full tracebacks to the terminal.

## Definition of done

- `python agent.py --selftest` passes (offline: JSON parse, prompt build, deterministic edit,
  router).
- The mocked-loop test passes (offline: reference never reaches the editor; content-change
  verdict triggers a refine; clean verdict stops the loop). See `docs/BUILD_PLAN.md`.
- `app.py` launches; a real source+reference run produces a re-graded image with the same
  content; JPG and ZIP downloads work at full resolution.

## Guardrails

- Confirm Gemini model IDs against **AI Studio** before hardcoding them — they change.
- Image generation needs a **billed (Tier-1)** project; assume that in setup docs, do not
  try to engineer around the free-tier zero quota.
- Prefer **Planning Mode** for multi-step tasks; ask for approval before large refactors.
