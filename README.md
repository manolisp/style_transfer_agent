# Style-Match Photo Editor Agent

An AI agent that applies the **tonal/colour style** of a reference image (or a text
instruction) to a photo **without changing the content** — same objects, same composition,
only the grade changes. Built for the Kaggle **5-Day AI Agents: Intensive Vibe Coding Course
with Google** capstone.

## Why
Generative "style transfer" usually repaints the scene — inventing objects, redrawing skies,
smoothing water. This agent transfers only tone/colour, then **checks its own work against the
original and corrects itself** if any content drifted.

## What it does
Give it a source photo plus a **style-reference image** or a **text instruction**. It runs a
`perceive → route → edit → critique → refine` loop, lets you steer with plain-language
feedback, and exports a full-resolution JPG (or a ZIP of every iteration).

## How it works
1. **Perceive** — a Gemini vision model turns the reference into a *tone-only* style spec (JSON).
2. **Route** — trivial edits (plain B&W, sepia, contrast) use a fast local Pillow tool; the rest go to the image model.
3. **Edit** — a "colorist" prompt re-grades the source with hard rules against adding, moving, or redrawing anything (Gemini image model / Nano Banana).
4. **Critique** — a Gemini vision model compares the edit to the **original** and scores tone match *and* content preservation, capping the score if content changed.
5. **Refine** — below threshold, it rewrites the edit prompt from the critique and retries.

A **human-in-the-loop** step then lets you type feedback that's fed back through the same refine path.

**Key design decision:** the reference image is **never sent to the image model** — the style
reaches the editor only as text, so the model has nothing to copy content from. This is what
stops the content leakage.

See `architecture_diagram.md` for the loop diagram.

## Quickstart
```bash
pip install google-genai pillow gradio
export GEMINI_API_KEY="your-key"        # Windows (cmd): set GEMINI_API_KEY=your-key
python agent.py --selftest              # offline checks, no key needed
python app.py                           # web UI at http://127.0.0.1:7860
```
Image generation requires a **billed (Tier-1)** Gemini API project — the free tier returns a
zero quota for image models. See `docs/SETUP.md` for details and troubleshooting.

## Project structure
```
agent.py                 # the agent: perceive / route / edit / critique / refine loop + CLI + --selftest
app.py                   # Gradio web UI (result, critique trace, downloads, feedback)
AGENTS.md                # always-on rules used when (re)building in Google Antigravity
architecture_diagram.md  # the loop diagram
docs/                    # SPEC, PROMPTS, BUILD_PLAN, SETUP (the Antigravity build package)
```

## Tech stack
Python · Gemini API (`google-genai`) · Pillow · Gradio. Built with vibe coding and
re-implemented spec-first in Google Antigravity.

## Course concepts demonstrated
Tool/API integration (image + vision models + a local tool, with routing) · evaluation (the
self-critique loop) · human-in-the-loop (feedback) · guardrails & robust error handling
(content-preservation rules, quota/safety-block/API handling).

## Notes
No API key is stored in this repo — the key is read from the environment only.
