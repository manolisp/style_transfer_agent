import os
import sys
import re
import json
import io
import argparse
from PIL import Image, ImageEnhance
from google import genai
from google.genai import types

# Model IDs
IMAGE_MODEL = "gemini-3.1-flash-image"
THINK_MODEL = "gemini-3.5-flash"

# Router Keywords
DETERMINISTIC_KEYWORDS = (
    "black and white", "grayscale", "greyscale", "b&w", "brighten",
    "darken", "contrast", "crop", "rotate", "sepia"
)

# Prompts copied verbatim from docs/PROMPTS.md
PERCEPTION_PROMPT = """You are a photo COLOR-GRADE analyst. Look at the reference image (and/or the user's
text instruction) and describe ONLY its tonal and color treatment -- the qualities that
could be reproduced on a DIFFERENT photo without changing that photo's content.

Return ONLY a JSON object -- no markdown, no backticks, no commentary -- with exactly
these keys:
{
  "color_mode": "black_and_white" | "color" | "sepia" | "duotone",
  "palette":    "the color grade / dominant tones, or n/a if black_and_white",
  "tonality":   "how shadows, midtones and highlights are mapped (e.g. soft glowing highlights, gentle blacks)",
  "contrast":   "low" | "medium" | "high",
  "grain":      "clean" | "fine grain" | "heavy grain / film-like",
  "mood":       "1-3 words, e.g. moody, serene, cinematic",
  "summary":    "one sentence describing ONLY the tone/color look"
}
Describe ONLY tone and color. Do NOT mention composition, objects, skies, clouds, water
texture, long exposure, motion blur, smoothing, sharpness, or anything about WHAT is in
the picture -- those are content, not style. If a text instruction conflicts with the
reference, the instruction wins. Base every field on what you actually see or are told."""

EDIT_TEMPLATE = """You are a film COLORIST. Your ONLY job is to re-grade the tones and colors of the
photograph provided. Treat this as a color-grading pass, NOT image generation.

Reproduce the input photograph EXACTLY -- pixel for pixel -- changing ONLY its global
tone and color to match this target look:
- Color mode: {color_mode}
- Palette / grade: {palette}
- Tonality: {tonality}
- Contrast: {contrast}
- Grain: {grain}
- Mood: {mood}

ABSOLUTE RULES -- the scene content must NOT change in any way:
- Do NOT add, remove, move, resize, or invent anything. Every object stays exactly where
  it is and as it is: the subject, every rock, the shoreline, the horizon, each cloud,
  the waves, reflections, and all textures and edges.
- Do NOT re-draw the sky or the water. Keep the exact same cloud shapes and the exact
  same water surface. Do NOT simulate long exposure, motion blur, or smoothing.
- The output must be pixel-aligned with the input: if the two were stacked, every edge
  would line up perfectly. You are ONLY adjusting brightness, contrast, and color/tone.

Output the same photograph, re-graded. Nothing added, nothing removed, nothing moved."""

CRITIC_PROMPT = """You are a strict photo-grading QA reviewer. You are given the ORIGINAL photo, the
EDITED photo, and the TARGET STYLE SPEC (JSON). Judge TWO things:
(1) TONE: does the edited photo's tone/color match the target spec?
(2) CONTENT: is the edited photo's content IDENTICAL to the original -- same objects in
    the same positions, same sky and same cloud shapes, same water surface, same edges --
    with ONLY tone/color changed?

Return ONLY a JSON object -- no markdown, no backticks -- with exactly these keys:
{
  "score": integer 0-10 (10 = tone matches the spec AND content is unchanged),
  "tone_match": true/false,
  "content_preserved": true/false,
  "content_changes": "none" | short list of anything added, removed, moved or re-rendered
                      (e.g. "new rocks in foreground", "clouds re-drawn", "water smoothed"),
  "biggest_gap": "the single most important problem to fix next",
  "fix_instruction": "ONE concrete, imperative sentence telling the editor how to fix it"
}
CONTENT CHANGE IS THE WORST FAILURE: if content_preserved is false, the score MUST be 4
or lower, no matter how good the tone looks. Compare the two images carefully."""

REFINE_PROMPT = """You improve image-edit prompts. Given the PREVIOUS EDIT PROMPT, the CRITIC VERDICT
(JSON), and the TARGET SPEC (JSON), rewrite the edit prompt so the next attempt fixes
the critic's "biggest_gap" and "fix_instruction" while keeping everything that already
worked.

Rules: keep the colorist framing and ALL the "ABSOLUTE RULES" about content preservation
exactly as they are; change only the tone/color guidance that needs fixing; be specific
and imperative; NEVER weaken the content-preservation rules -- if the critic reported a
content change, make those rules even firmer. Return ONLY the new edit prompt text -- no
explanation, no JSON, no backticks."""

# Lazy client storage
_client = None

def get_client() -> genai.Client:
    """Returns the GenAI client, initializing it lazily on the first call."""
    global _client
    if _client is None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError(
                "GEMINI_API_KEY environment variable is not set. "
                "Please set it to run operations requiring the Gemini API."
            )
        _client = genai.Client()
    return _client

def parse_json(text: str) -> dict:
    """Parses JSON defensively: strips ```json / ``` fences and extracts the first {...} block."""
    text_clean = text.strip()
    if text_clean.startswith("```"):
        text_clean = re.sub(r"^```(?:json)?\s*", "", text_clean)
        text_clean = re.sub(r"\s*```$", "", text_clean)
    
    try:
        return json.loads(text_clean)
    except json.JSONDecodeError:
        pass
        
    # Extract the first substring that looks like a JSON block
    match = re.search(r"(\{.*\})", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
            
    # Try finding between first '{' and last '}'
    start_idx = text.find('{')
    if start_idx != -1:
        end_idx = text.rfind('}')
        if end_idx > start_idx:
            try:
                return json.loads(text[start_idx:end_idx+1])
            except json.JSONDecodeError:
                pass
                
    raise ValueError(f"Could not parse JSON from response text: {text!r}")

def load_image(image_input) -> Image.Image:
    """Loads an image from a filepath, bytes, BytesIO, or returns it if already a PIL Image."""
    if isinstance(image_input, Image.Image):
        return image_input.convert("RGB")
    if isinstance(image_input, str):
        return Image.open(image_input).convert("RGB")
    if isinstance(image_input, bytes):
        return Image.open(io.BytesIO(image_input)).convert("RGB")
    if hasattr(image_input, "read"):
        return Image.open(image_input).convert("RGB")
    raise TypeError(f"Unsupported image input type: {type(image_input)}")

def perceive(reference: Image.Image | None, instruction: str | None) -> dict:
    """Uses THINK_MODEL and PERCEPTION_PROMPT to analyse target style, returning a StyleSpec dict."""
    if reference is None and not instruction:
        raise ValueError("Must provide at least one of reference image or text instruction.")
        
    client = get_client()
    contents = [PERCEPTION_PROMPT]
    if reference is not None:
        contents.append(reference)
    if instruction:
        contents.append(f"User instruction: {instruction}")
        
    resp = client.models.generate_content(
        model=THINK_MODEL,
        contents=contents
    )
    return parse_json(resp.text)

def plan(spec: dict, instruction: str | None, has_reference: bool) -> str:
    """Routes the task to 'deterministic' or 'generative' using a simple keyword heuristic."""
    if has_reference:
        return "generative"
    if not instruction:
        return "generative"
        
    inst_lower = instruction.lower()
    for kw in DETERMINISTIC_KEYWORDS:
        if kw in inst_lower:
            return "deterministic"
    return "generative"

def deterministic_edit(img: Image.Image, spec: dict) -> Image.Image:
    """Applies basic Pillow edits (B&W and/or contrast enhancement) based on the StyleSpec."""
    edited = img.convert("RGB")
    
    color_mode = spec.get("color_mode", "color")
    if color_mode in ("black_and_white", "grayscale", "greyscale"):
        edited = edited.convert("L").convert("RGB")
    elif color_mode == "sepia":
        # Fast, pixel-perfect sepia mapping using a weighted grayscale base
        l_band = edited.convert("L")
        r = l_band.point(lambda i: int(i * 240 / 255))
        g = l_band.point(lambda i: int(i * 200 / 255))
        b = l_band.point(lambda i: int(i * 145 / 255))
        edited = Image.merge("RGB", (r, g, b))
        
    contrast_mode = spec.get("contrast", "medium")
    contrast_factors = {
        "low": 0.85,
        "medium": 1.0,
        "high": 1.25
    }
    factor = contrast_factors.get(contrast_mode, 1.0)
    if factor != 1.0:
        enhancer = ImageEnhance.Contrast(edited)
        edited = enhancer.enhance(factor)
        
    return edited

def build_edit_prompt(spec: dict, has_reference: bool = False) -> str:
    """Fills the EDIT_TEMPLATE with fields from the StyleSpec."""
    return EDIT_TEMPLATE.format(
        color_mode=spec.get("color_mode", "color"),
        palette=spec.get("palette", "n/a"),
        tonality=spec.get("tonality", ""),
        contrast=spec.get("contrast", "medium"),
        grain=spec.get("grain", "clean"),
        mood=spec.get("mood", "")
    )

def generative_edit(source: Image.Image, reference: Image.Image | None, prompt: str) -> Image.Image:
    """Sends the source image and prompt to the IMAGE_MODEL, ignoring the reference image."""
    client = get_client()
    
    # Critical invariant: NEVER pass the reference image to the image model
    contents = [prompt, source]
    
    config = types.GenerateContentConfig(
        response_modalities=["TEXT", "IMAGE"]
    )
    
    resp = client.models.generate_content(
        model=IMAGE_MODEL,
        contents=contents,
        config=config
    )
    
    # Robust error handling:
    # 1. Prompt blocked check
    if hasattr(resp, 'prompt_feedback') and resp.prompt_feedback and getattr(resp.prompt_feedback, 'block_reason', None):
        raise RuntimeError(f"Prompt blocked: {resp.prompt_feedback.block_reason}")
        
    if not resp.candidates:
        raise RuntimeError("No candidates returned from the image model.")
        
    candidate = resp.candidates[0]
    
    # 2. Candidate safety block check (content is None)
    if candidate.content is None:
        finish_reason = getattr(candidate, 'finish_reason', 'SAFETY')
        raise RuntimeError(f"Generative edit blocked by safety filters. Finish reason: {finish_reason}")
        
    # 3. Check for parts
    parts = getattr(candidate.content, 'parts', None)
    if not parts:
        raise RuntimeError("Candidate content has no parts.")
        
    # 4. Check for inline_data
    for part in parts:
        if hasattr(part, 'inline_data') and part.inline_data and hasattr(part.inline_data, 'data'):
            return Image.open(io.BytesIO(part.inline_data.data)).convert("RGB")
            
    # 5. Check if it's text-only
    text_parts = [part.text for part in parts if hasattr(part, 'text') and part.text]
    if text_parts:
        joined_text = " ".join(text_parts)
        raise RuntimeError(
            f"Image model returned text instead of an image: {joined_text}. "
            "Hint: Make sure the model supports response_modalities=['TEXT','IMAGE']."
        )
        
    raise RuntimeError("No image data found in model response parts.")

def critique(edited: Image.Image, spec: dict, source: Image.Image) -> dict:
    """Uses THINK_MODEL to grade tone match and content preservation, returning a Verdict dict."""
    client = get_client()
    contents = [
        CRITIC_PROMPT,
        "ORIGINAL PHOTO:",
        source,
        "EDITED PHOTO:",
        edited,
        "TARGET STYLE SPEC:",
        json.dumps(spec)
    ]
    resp = client.models.generate_content(
        model=THINK_MODEL,
        contents=contents
    )
    verdict = parse_json(resp.text)
    
    # Hard invariant: if content is not preserved, the score must be <= 4
    if not verdict.get("content_preserved", True):
        verdict["score"] = min(verdict.get("score", 0), 4)
        
    return verdict

def refine_prompt(prev: str, verdict: dict, spec: dict) -> str:
    """Uses THINK_MODEL to refine the edit prompt based on the critic verdict."""
    client = get_client()
    contents = [
        REFINE_PROMPT,
        "PREVIOUS EDIT PROMPT:\n" + prev,
        "CRITIC VERDICT:\n" + json.dumps(verdict),
        "TARGET SPEC:\n" + json.dumps(spec)
    ]
    resp = client.models.generate_content(
        model=THINK_MODEL,
        contents=contents
    )
    return resp.text.strip()

def run(source: Image.Image, reference: Image.Image | None, instruction: str | None,
        max_iters: int = 5, threshold: int = 8, on_iter: callable = None) -> tuple[Image.Image, dict, list[dict]]:
    """Runs the full perceive-plan-edit loop."""
    spec = perceive(reference, instruction)
    route = plan(spec, instruction, reference is not None)
    
    if route == "deterministic":
        edited = deterministic_edit(source, spec)
        if on_iter:
            on_iter(0, edited)
        return edited, spec, []
        
    prompt = build_edit_prompt(spec)
    history = []
    edited = source
    current_fix_instruction = None
    
    for i in range(max_iters):
        prompt_for_edit = prompt
        if current_fix_instruction:
            prompt_for_edit += f"\n\nCRITIC FIX REQUIREMENT: {current_fix_instruction}"
            
        edited = generative_edit(source, None, prompt_for_edit)
        if on_iter:
            on_iter(i, edited)
            
        verdict = critique(edited, spec, source)
        history.append({
            "iter": i,
            "prompt": prompt_for_edit,
            "verdict": verdict,
            "image": edited
        })
        
        if verdict.get("score", 0) >= threshold:
            break
            
        if i < max_iters - 1:
            prompt = refine_prompt(prompt, verdict, spec)
            current_fix_instruction = verdict.get("fix_instruction")
            
    return edited, spec, history

def selftest():
    """Runs offline self-test checks without invoking the Gemini API."""
    print("Running offline self-test...")
    
    # 1. Test parse_json
    try:
        res = parse_json('```json\n{"color_mode": "color", "palette": "grade"}\n```')
        assert res.get("color_mode") == "color"
        
        res = parse_json('Prose {"color_mode": "sepia"} Prose')
        assert res.get("color_mode") == "sepia"
        print("PASS: parse_json")
    except Exception as e:
        print(f"FAIL: parse_json ({e})")
        sys.exit(1)
        
    # 2. Test build_edit_prompt
    try:
        spec = {
            "color_mode": "color",
            "palette": "warm golds",
            "tonality": "soft highlights",
            "contrast": "high",
            "grain": "clean",
            "mood": "warm"
        }
        prompt = build_edit_prompt(spec)
        assert "COLORIST" in prompt
        assert "content must NOT change" in prompt
        print("PASS: build_edit_prompt")
    except Exception as e:
        print(f"FAIL: build_edit_prompt ({e})")
        sys.exit(1)
        
    # 3. Test plan / router
    try:
        assert plan({}, "make it black and white", False) == "deterministic"
        assert plan({}, "grayscaled style", False) == "deterministic"
        assert plan({}, "brighten up the shadows", False) == "deterministic"
        assert plan({}, "sepia tone", False) == "deterministic"
        
        assert plan({}, "cinematic moody", False) == "generative"
        assert plan({}, "make it black and white", True) == "generative"
        print("PASS: plan / router")
    except Exception as e:
        print(f"FAIL: plan / router ({e})")
        sys.exit(1)
        
    # 4. Test deterministic_edit
    try:
        test_img = Image.new("RGB", (10, 10), color=(100, 150, 200))
        out_bw = deterministic_edit(test_img, {"color_mode": "black_and_white", "contrast": "high"})
        assert out_bw.size == (10, 10)
        assert out_bw.mode == "RGB"
        r, g, b = out_bw.getpixel((5, 5))
        assert r == g == b
        
        out_sepia = deterministic_edit(test_img, {"color_mode": "sepia"})
        assert out_sepia.size == (10, 10)
        assert out_sepia.mode == "RGB"
        print("PASS: deterministic_edit")
    except Exception as e:
        print(f"FAIL: deterministic_edit ({e})")
        sys.exit(1)
        
    # 5. Test mocked loop (no API calls)
    try:
        import json as json_mod
        from unittest.mock import MagicMock
        
        spec_json = json_mod.dumps({
            "color_mode": "color",
            "palette": "warm golds",
            "tonality": "soft highlights",
            "contrast": "high",
            "grain": "clean",
            "mood": "warm",
            "summary": "Warm golden tone"
        })
        
        verdict1_json = json_mod.dumps({
            "score": 3,
            "tone_match": False,
            "content_preserved": False,
            "content_changes": "rocks added",
            "biggest_gap": "rocks added",
            "fix_instruction": "Keep background rocks unchanged."
        })
        
        verdict2_json = json_mod.dumps({
            "score": 9,
            "tone_match": True,
            "content_preserved": True,
            "content_changes": "none",
            "biggest_gap": "none",
            "fix_instruction": "None"
        })
        
        img = Image.new("RGB", (10, 10), color=(255, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png_bytes = buf.getvalue()
        
        critic_count = 0
        captured_image_calls = []
        
        def mock_generate_content(model, contents, config=None):
            nonlocal critic_count
            if model == THINK_MODEL:
                prompt = contents[0]
                if PERCEPTION_PROMPT in prompt:
                    mock_resp = MagicMock()
                    mock_resp.text = spec_json
                    return mock_resp
                elif CRITIC_PROMPT in prompt:
                    critic_count += 1
                    mock_resp = MagicMock()
                    mock_resp.text = verdict1_json if critic_count == 1 else verdict2_json
                    return mock_resp
                elif REFINE_PROMPT in prompt:
                    mock_resp = MagicMock()
                    mock_resp.text = "Refined prompt text"
                    return mock_resp
            elif model == IMAGE_MODEL:
                captured_image_calls.append(contents)
                mock_part = MagicMock()
                mock_part.inline_data.data = png_bytes
                if hasattr(mock_part, "text"):
                    del mock_part.text
                mock_candidate = MagicMock()
                mock_candidate.content.parts = [mock_part]
                mock_candidate.finish_reason = "STOP"
                
                mock_resp = MagicMock()
                mock_resp.candidates = [mock_candidate]
                mock_resp.prompt_feedback = None
                return mock_resp
            return MagicMock()
            
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = mock_generate_content
        
        global _client
        original_client = _client
        _client = mock_client
        
        try:
            source = Image.new("RGB", (100, 100), color=(128, 128, 128))
            ref = Image.new("RGB", (150, 150), color=(200, 100, 50))
            
            edited_res, spec_res, history_res = run(
                source=source,
                reference=ref,
                instruction="Warm style",
                max_iters=3,
                threshold=8
            )
            
            assert len(captured_image_calls) == 2
            for contents in captured_image_calls:
                assert contents[1] == source
                assert all(item is not ref for item in contents)
                
            assert len(history_res) == 2
            assert history_res[0]["verdict"]["score"] == 3
            assert history_res[1]["verdict"]["score"] == 9
            
            captured_image_calls.clear()
            edited_det, spec_det, history_det = run(
                source=source,
                reference=None,
                instruction="make it black and white",
                max_iters=3,
                threshold=8
            )
            assert len(captured_image_calls) == 0
            assert len(history_det) == 0
            print("PASS: mocked_loop")
        finally:
            _client = original_client
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"FAIL: mocked_loop ({e})")
        sys.exit(1)
        
    print("ALL TESTS PASSED!")

def main():
    parser = argparse.ArgumentParser(description="Style-Match Photo Editor Agent CLI")
    parser.add_argument("source", nargs="?", help="Path to the source photo")
    parser.add_argument("--reference", help="Path to the style reference image")
    parser.add_argument("--instruction", help="Text description of the target style")
    parser.add_argument("--out", default="out.jpg", help="Path to save the final edited image")
    parser.add_argument("--max-iters", type=int, default=5, help="Max loop iterations")
    parser.add_argument("--threshold", type=int, default=8, help="Quality threshold score (0-10)")
    parser.add_argument("--no-human", action="store_true", help="Disable human-in-the-loop feedback in CLI")
    parser.add_argument("--save-iters", action="store_true", help="Save intermediate iteration images")
    parser.add_argument("--selftest", action="store_true", help="Run offline tests and exit")
    
    args = parser.parse_args()
    
    if args.selftest:
        selftest()
        sys.exit(0)
        
    if not args.source:
        parser.print_help()
        sys.exit(1)
        
    print(f"Loading source image: {args.source}")
    source_img = load_image(args.source)
    
    ref_img = None
    if args.reference:
        print(f"Loading reference image: {args.reference}")
        ref_img = load_image(args.reference)
        
    if not ref_img and not args.instruction:
        print("Error: Must provide at least one of --reference or --instruction.")
        sys.exit(1)
        
    def on_iter(i, img):
        print(f"Iteration {i} complete.")
        if args.save_iters:
            name = f"iter_{i}.jpg"
            img.save(name, quality=95, subsampling=0)
            print(f"Saved iteration {i} to {name}")
            
    print("Running editor loop...")
    edited, spec, history = run(
        source=source_img,
        reference=ref_img,
        instruction=args.instruction,
        max_iters=args.max_iters,
        threshold=args.threshold,
        on_iter=on_iter
    )
    
    edited.save(args.out, quality=95, subsampling=0)
    print(f"Saved result to {args.out}")
    print(f"StyleSpec: {json.dumps(spec, indent=2)}")
    
    if history:
        print("\nCritique Trace:")
        for h in history:
            print(f"  Iteration {h['iter']}: Score = {h['verdict'].get('score')}, Preserve = {h['verdict'].get('content_preserved')}, Gap = {h['verdict'].get('biggest_gap')}")
            
    if not args.no_human and history:
        last_prompt = history[-1]["prompt"]
        current_img = edited
        iter_num = len(history)
        
        while True:
            feedback = input("\nEnter feedback to refine image (or press Enter/type 'ok' to finish): ").strip()
            if not feedback or feedback.lower() == "ok":
                break
                
            print("Applying human feedback...")
            feedback_verdict = {
                "score": 5,
                "tone_match": False,
                "content_preserved": True,
                "content_changes": "none",
                "biggest_gap": feedback,
                "fix_instruction": feedback
            }
            
            new_prompt = refine_prompt(last_prompt, feedback_verdict, spec)
            print(f"New refined prompt: {new_prompt}")
            
            current_img = generative_edit(source_img, None, new_prompt)
            if args.save_iters:
                name = f"iter_human_{iter_num}.jpg"
                current_img.save(name, quality=95, subsampling=0)
                print(f"Saved feedback iteration to {name}")
                
            verdict = critique(current_img, spec, source_img)
            print(f"New score: {verdict.get('score')} (Content preserved: {verdict.get('content_preserved')})")
            print(f"Biggest gap: {verdict.get('biggest_gap')}")
            
            current_img.save(args.out, quality=95, subsampling=0)
            print(f"Saved updated result to {args.out}")
            
            last_prompt = new_prompt
            iter_num += 1

if __name__ == "__main__":
    main()
