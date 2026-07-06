# SPEC.md — Style-Match Photo Editor Agent

The complete specification. `PROMPTS.md` holds the verbatim prompts referenced here.

## 1. Goal & users

An agent that applies the **tonal/colour style** of a reference image (or a text
instruction) to a source photo while keeping the **content identical**. Built as a capstone
demonstration of an agentic loop: tool use, self-evaluation, human-in-the-loop, and error
handling. Single local user; desktop web UI.

## 2. Functional requirements

### 2.1 Inputs
- **Source photo** (required): the image to edit.
- **Style reference image** (optional) — its *look* is the target.
- **Text instruction** (optional) — e.g. "moody black and white, faded blacks".
- At least one of {reference, instruction} must be provided.
- Controls: `max_iters` (1–4, default 3), `threshold` (1–10, default 8).

### 2.2 The agent loop (see §4 for detail)
1. **Perceive** the target style → a tone-only `StyleSpec` (JSON).
2. **Route**: trivial deterministic ops (B&W, contrast, etc.) → a Pillow tool; everything
   else → the generative editor.
3. **Edit**: re-grade the source to match the spec (content-locked).
4. **Critique**: compare the edit to the **original**; score tone match AND content
   preservation.
5. **Refine**: if below threshold, rewrite the edit prompt from the critique and retry, up
   to `max_iters`.

### 2.3 Human-in-the-loop
After the automated loop, the user can type feedback ("keep more shadow detail"); the app
treats it as a critique, refines the prompt, regenerates once, and shows the result. Repeats
until the user approves.

### 2.4 Outputs & downloads
- Show the final result, a **critique trace** (per-iteration score + biggest gap), the
  `StyleSpec`, and a **gallery of every iteration**.
- **Download result (JPG)** — the current/final image at full resolution, JPEG quality 95,
  subsampling 0 (4:4:4).
- **Download all iterations (ZIP)** — every iteration + the final, each a full-res JPG.

### 2.5 UI (Gradio v6.x)
- Left column: source upload, reference upload, instruction textbox, two sliders, "Edit
  photo" button.
- Right column: result image; the two download buttons; critique trace (Markdown); an
  accordion with the `StyleSpec` (JSON) and the iterations gallery; a feedback textbox +
  "Apply feedback" button.
- Gallery: `allow_preview=True`, `object_fit="contain"`, ~3 columns. **Note:** Gradio 6.x
  has no `show_fullscreen_button`/`show_download_button` params — do not pass them. Clicking a
  thumbnail opens an in-page preview with a close **X** (Esc also exits); the dedicated JPG
  button is the reliable download path.

## 3. Data structures

### 3.1 StyleSpec (produced by Perceive; tone/colour ONLY)
```json
{
  "color_mode": "black_and_white | color | sepia | duotone",
  "palette":    "grade / dominant tones, or n/a if black_and_white",
  "tonality":   "how shadows/midtones/highlights are mapped",
  "contrast":   "low | medium | high",
  "grain":      "clean | fine grain | heavy grain / film-like",
  "mood":       "1-3 words",
  "summary":    "one sentence describing only the tone/colour look"
}
```
No keys for composition, lighting direction, objects, or effects that change content.

### 3.2 Verdict (produced by Critique)
```json
{
  "score": 0,                    // int 0-10
  "tone_match": true,
  "content_preserved": true,
  "content_changes": "none | e.g. 'new rocks in foreground', 'clouds re-drawn'",
  "biggest_gap": "the single most important problem to fix next",
  "fix_instruction": "ONE imperative sentence"
}
```
Rule: if `content_preserved` is false, `score` MUST be ≤4.

## 4. Technical architecture

### 4.1 Modules
- **`agent.py`** — pure logic, no UI, importable and testable headless. Public functions:
  `perceive`, `plan`, `deterministic_edit`, `generative_edit`, `critique`, `refine_prompt`,
  `build_edit_prompt`, `run`, plus `parse_json`, `load_image`, and a `selftest`.
- **`app.py`** — Gradio UI importing `agent`; owns `do_run`, `apply_feedback`, the JPG/ZIP
  download helpers, error-surfacing wrapper, and the Blocks layout.

### 4.2 Models (confirm current IDs in AI Studio)
- `IMAGE_MODEL` — a Gemini Flash **Image** model, e.g. `gemini-2.5-flash-image` (Nano Banana)
  or newer `gemini-3.1-flash-image`. **Requires a billed project** (see `SETUP.md`).
- `THINK_MODEL` — a Gemini Flash reasoning+vision model, e.g. `gemini-2.5-flash`, used for
  perceive/critique/refine.

### 4.3 Gemini API call patterns (`google-genai`)
- Client: `from google import genai; client = genai.Client()` (reads `GEMINI_API_KEY`).
  Create it **lazily** via an accessor so imports don't need a key.
- Text/vision: `client.models.generate_content(model=THINK_MODEL, contents=[PROMPT, image, "text"])`
  then read `resp.text`.
- Image edit: `client.models.generate_content(model=IMAGE_MODEL, contents=[prompt, source])`
  — **source only, no reference**. Iterate `resp.candidates[0].content.parts`; return the
  part whose `inline_data` is set (`Image.open(BytesIO(part.inline_data.data))`).

### 4.4 The loop (pseudocode)
```
def run(source, reference=None, instruction=None, max_iters=3, threshold=8, on_iter=None):
    spec  = perceive(reference, instruction)           # tone-only StyleSpec
    route = plan(spec, instruction, reference is not None)
    if route == "deterministic":
        edited = deterministic_edit(source, spec); on_iter(0, edited); return edited, spec, []
    prompt, history = build_edit_prompt(spec), []
    for i in range(max_iters):
        edited  = generative_edit(source, None, prompt)   # reference NOT passed
        on_iter(i, edited)
        verdict = critique(edited, spec, source)          # compare to ORIGINAL
        history.append({"iter": i, "prompt": prompt, "verdict": verdict})
        if verdict["score"] >= threshold: break
        if i < max_iters - 1: prompt = refine_prompt(prompt, verdict, spec)
    return edited, spec, history
```

### 4.5 Router (deterministic vs generative)
Keyword heuristic, no API call: if **no reference** and the instruction contains any of
`black and white, grayscale, greyscale, b&w, brighten, darken, contrast, crop, rotate,
sepia` → `deterministic`; otherwise → `generative`. A reference always → `generative`.

### 4.6 Deterministic edit (Pillow)
Convert to RGB; if `color_mode == black_and_white`, `convert("L").convert("RGB")`; apply a
contrast factor from {low:0.85, medium:1.0, high:1.25} via `ImageEnhance.Contrast`.

## 5. Design decisions & rationale (the improvements — keep all)

- **Reference image withheld from the editor.** Image-to-image "style transfer" makes the
  model import the reference's content (this produced invented foreground rocks and re-drawn
  clouds/water in testing). Feeding only the source + a text style spec eliminates the leak.
- **Editor reframed as a colorist.** The edit prompt demands a pixel-aligned tonal re-grade
  with explicit prohibitions (no add/remove/move, no sky/water re-draw, no long-exposure
  smoothing). See `PROMPTS.md`.
- **Perception is tone-only.** Prevents content-changing descriptors (e.g. "long exposure")
  from ever entering the spec.
- **Critic sees the original and checks content.** Detects drift automatically and caps the
  score, so the refine loop corrects it instead of the user catching it by eye.
- **Robust error handling.** Lazy client; defensive JSON parsing; explicit handling of
  safety-blocked responses (candidate `content is None`) with `finish_reason`; UI toast
  surfacing + terminal tracebacks.
- **Full-res JPG/ZIP downloads** because Gradio's built-in image download is format/size
  unreliable and v6 removed the gallery download toggle.

## 6. Non-goals
- No long-exposure/motion effects (that would change content). If the user wants silky water
  they must ask for it explicitly or shoot a real long exposure — it is never inherited from
  the reference.
- No multi-user accounts, no persistence/database, no cloud deploy in the base build
  (optional stretch: Cloud Run — see `SETUP.md`).
- No upscaling beyond the model's native output resolution in the base build.
