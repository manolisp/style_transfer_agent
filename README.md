# Style-Match Photo Editor Agent — Antigravity Build Package

This folder is a **spec-driven build package** for re-implementing the Style-Match Photo
Editor Agent from scratch inside **Google Antigravity**. It contains everything an
Antigravity agent needs to plan, build, and verify the app — the same behaviour and all
the hardening from the reference implementation.

## What you're building (one paragraph)

A desktop web app (Gradio UI + a Python agent) that takes a **source photo** and either a
**style-reference image** or a **text instruction**, then re-grades the photo to match that
look **without changing any content**. It runs an agentic loop — perceive the target style,
edit, self-critique against the original, refine — with a human-in-the-loop feedback step
and full-resolution JPG/ZIP downloads.

## The documents (read in this order)

| File | Purpose | Antigravity role |
|------|---------|------------------|
| `AGENTS.md` | Always-on rules & hard invariants the agent must never break | Copy to **project root** (Antigravity auto-loads it) |
| `SPEC.md` | Full functional + technical specification (the source of truth) | The "specify" artifact to review/approve |
| `PROMPTS.md` | The four LLM prompts, **verbatim** — the heart of the agent | Reproduce exactly; do not paraphrase |
| `BUILD_PLAN.md` | Ordered task list with acceptance criteria + how to drive Antigravity | The "tasks" artifact to implement against |
| `SETUP.md` | Environment, API key, billing, model IDs, run & verify, troubleshooting | Setup + verification reference |

## How to use this in Antigravity

1. **Create the workspace.** Make an empty project folder and open it in Antigravity
   (Open Workspace). Copy `AGENTS.md` to the project root. Put the other four docs in a
   `docs/` subfolder (or anywhere in the workspace — the agent can read them).
2. **Start in Planning Mode.** Open the Agent Manager, start a conversation in this
   workspace, pick a Gemini 3 Pro/Flash model, and choose **Planning Mode** (review the
   plan before code is written). Review-driven autonomy is a good default for a first build.
3. **Kick it off** with the prompt in `BUILD_PLAN.md` → "Kickoff prompt". It points the
   agent at `SPEC.md`, `PROMPTS.md`, and the task list, and tells it to build task by task
   and verify each with the acceptance criteria.
4. **Verify** using `SETUP.md` → the `--selftest` and mocked-loop checks (no API calls),
   then a real end-to-end run once billing/key are set.

## Non-negotiables (also enforced in AGENTS.md)

- The style-reference image is **never** sent to the image-generation model (content-leak
  prevention). Style is conveyed only as text derived from the reference.
- The image edit is a **content-locked tonal re-grade**, not image generation.
- The critic compares the edit against the **original** and fails any content change.
- API keys come from the environment, never hardcoded. Gradio state is in-memory only.
- Image generation requires a **billed (Tier-1)** Gemini API project; the free tier returns
  a zero quota for image models. See `SETUP.md`.
