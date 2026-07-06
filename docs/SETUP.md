# SETUP.md — environment, keys, running, troubleshooting

## 1. Prerequisites
- **Python 3.11+** (the reference machine ran 3.14; any 3.11+ is fine).
- A **Google AI Studio API key** with **image generation enabled** — see §3.
- Antigravity installed (antigravity.google) if you're building there.

## 2. Install dependencies
Using `uv` (recommended in Antigravity) or `pip`:
```bash
# uv
uv venv && uv pip install google-genai pillow gradio
# or pip
pip install google-genai pillow gradio
```

## 3. API key + billing (image generation needs a billed project)
Text/vision calls work on the free tier, but **image models return a zero free-tier quota**
(you'll see `429 RESOURCE_EXHAUSTED ... limit: 0`). To generate images you must enable
billing so the project moves to **Tier 1**:
- In AI Studio / Google Cloud Console, link a billing account to the project that owns your
  key (a $0 spending cap is fine; there's no minimum spend). Tier-1 activation is ~instant.
- Cost is small: ~US$0.04 per image for `gemini-2.5-flash-image`. A full build/test session
  is cents to a couple of dollars.
- Quota is **per project, not per key** — a new key won't grant fresh quota.

Set the key in the **same shell** you run from:
```bash
# macOS / Linux
export GEMINI_API_KEY="AIza...yourkey"
# Windows cmd  (NO quotes -- quotes become part of the value)
set GEMINI_API_KEY=AIza...yourkey
# Windows PowerShell
$env:GEMINI_API_KEY="AIza...yourkey"
```

## 4. Model IDs (confirm current values in AI Studio)
Set these at the top of `agent.py`:
- `IMAGE_MODEL = "gemini-2.5-flash-image"`  (Nano Banana) — or newer `gemini-3.1-flash-image`.
- `THINK_MODEL = "gemini-2.5-flash"`  — or the newest Flash reasoning model listed.

These change often. To list what your key actually offers:
```python
from google import genai
for m in genai.Client().models.list():
    print(m.name)
```

## 5. Run
```bash
python agent.py --selftest          # offline checks, no key needed
python agent.py photo.jpg --reference ref.jpg          # CLI single edit
python app.py                       # web UI at http://127.0.0.1:7860
```

## 6. Verify (definition of done)
- `python agent.py --selftest` → all PASS.
- Mocked-loop test (Task 4 in `BUILD_PLAN.md`) → PASS.
- Real run: source + reference → re-graded image, same content; JPG + ZIP download at full
  resolution; a feedback refinement works.

## 7. Troubleshooting quick-reference (errors seen during the reference build)
| Symptom | Cause | Fix |
|---|---|---|
| `400 API_KEY_INVALID` | key not set, or quotes baked in via Windows `set` | print `repr(os.environ["GEMINI_API_KEY"])`; set without quotes; regenerate at aistudio.google.com/apikey |
| `429 RESOURCE_EXHAUSTED ... limit: 0` on the image model | free tier grants zero image quota | enable billing → Tier 1 (§3); waiting does not help |
| `AttributeError: 'NoneType' has no attribute 'parts'` | candidate `content` is `None` (safety block) | handle it: read `finish_reason`, raise a clear message (Task 3) |
| Model returns text, no image | SDK wants explicit modalities | add `config=types.GenerateContentConfig(response_modalities=['TEXT','IMAGE'])` |
| Gallery fullscreen "no return" | Gradio 6 built-in fullscreen | click X or press Esc; use the JPG download button instead |
| Blank error toast in the UI | Gradio hides exceptions | wrap callbacks to re-raise as `gr.Error(str(e))`; `demo.launch(show_error=True)`; read the terminal traceback |

## 8. Content-preservation expectation (important)
The reference's *long-exposure* look (glassy water, streaked clouds) is **content**, not
style, and is intentionally **not** reproduced — you get your exact scene re-graded to the
target tone. To get silky water you must ask for it explicitly (or shoot a real long
exposure); it is never inherited from the reference.

## 9. Optional: Cloud Run deploy (stretch)
Add `requirements.txt` (`google-genai`, `pillow`, `gradio`), set
`demo.launch(server_name="0.0.0.0", server_port=8080)`, containerise, and deploy. Keep
`GEMINI_API_KEY` as a runtime secret, never in the image.
