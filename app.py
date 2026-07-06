import os
import sys
import traceback
import tempfile
import zipfile
import gradio as gr
from PIL import Image

# Import the agent logic
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent

# Set up the exception wrapper for callbacks
def ui_callback(func):
    import inspect
    if inspect.isgeneratorfunction(func):
        def generator_wrapper(*args, **kwargs):
            try:
                yield from func(*args, **kwargs)
            except gr.Error:
                raise
            except Exception as e:
                traceback.print_exc()
                raise gr.Error(f"Error: {str(e)}")
        return generator_wrapper
    else:
        def normal_wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except gr.Error:
                raise
            except Exception as e:
                traceback.print_exc()
                raise gr.Error(f"Error: {str(e)}")
        return normal_wrapper

# Result download helpers
def _save_jpg(img, name_or_path):
    """Saves PIL Image as JPEG with quality=95, subsampling=0, and no resize."""
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.save(name_or_path, format="JPEG", quality=95, subsampling=0)

@ui_callback
def generate_jpg(state):
    if not state or not state.get("result"):
        raise gr.Error("No result to download. Please run the edit loop first.")
        
    temp_dir = tempfile.gettempdir()
    output_path = os.path.join(temp_dir, "graded_result.jpg")
    _save_jpg(state["result"], output_path)
    return output_path

@ui_callback
def generate_zip(state):
    if not state or not state.get("iters"):
        raise gr.Error("No iterations to download. Please run the edit loop first.")
        
    temp_dir = tempfile.gettempdir()
    zip_path = os.path.join(temp_dir, "iterations.zip")
    
    with zipfile.ZipFile(zip_path, "w") as zipf:
        for idx, img in enumerate(state["iters"]):
            img_path = os.path.join(temp_dir, f"iter_{idx + 1}.jpg")
            _save_jpg(img, img_path)
            zipf.write(img_path, f"iter_{idx + 1}.jpg")
            try:
                os.remove(img_path)
            except Exception:
                pass
                
        if state.get("result"):
            final_path = os.path.join(temp_dir, "final.jpg")
            _save_jpg(state["result"], final_path)
            zipf.write(final_path, "final.jpg")
            try:
                os.remove(final_path)
            except Exception:
                pass
                
    return zip_path

@ui_callback
def do_run(source_img, ref_img, instruction, max_iters, threshold, state):
    if source_img is None:
        raise gr.Error("Please upload a source photo first.")
    if ref_img is None and not instruction:
        raise gr.Error("Please upload a style reference image or enter a text instruction.")
        
    # Initialize state
    state = {
        "source": source_img,
        "reference": ref_img,
        "spec": None,
        "history": [],
        "last_prompt": "",
        "result": None,
        "iters": []
    }
    
    # Yield initial clear screen
    yield None, [], {}, "### Running Perception...", state
    
    # 1. Perceive style
    try:
        spec = agent.perceive(ref_img, instruction)
    except Exception as e:
        raise gr.Error(f"Style perception failed: {e}")
        
    state["spec"] = spec
    
    # Yield perceived spec
    yield None, [], spec, "### Perceived style spec. Choosing routing plan...", state
    
    # 2. Plan route
    route = agent.plan(spec, instruction, ref_img is not None)
    
    if route == "deterministic":
        try:
            edited = agent.deterministic_edit(source_img, spec)
        except Exception as e:
            raise gr.Error(f"Deterministic edit failed: {e}")
            
        state["result"] = edited
        state["iters"] = [edited]
        trace_text = "### Critique Trace\nDeterministic edit applied successfully (no iterations)."
        yield edited, [edited], spec, trace_text, state
        return
        
    # Generative loop
    prompt = agent.build_edit_prompt(spec)
    state["last_prompt"] = prompt
    current_fix_instruction = None
    
    for i in range(max_iters):
        yield state["result"], state["iters"], spec, f"### Editing iteration {i + 1} of {max_iters}...", state
        
        prompt_for_edit = prompt
        if current_fix_instruction:
            prompt_for_edit += f"\n\nCRITIC FIX REQUIREMENT: {current_fix_instruction}"
            
        try:
            edited = agent.generative_edit(source_img, None, prompt_for_edit)
        except Exception as e:
            raise gr.Error(f"Generative edit at iteration {i + 1} failed: {e}")
            
        state["iters"].append(edited)
        state["result"] = edited
        
        # Critique
        yield edited, state["iters"], spec, f"### Critiquing iteration {i + 1}...", state
        
        try:
            verdict = agent.critique(edited, spec, source_img)
        except Exception as e:
            raise gr.Error(f"Critique at iteration {i + 1} failed: {e}")
            
        state["history"].append({
            "iter": i,
            "prompt": prompt_for_edit,
            "verdict": verdict,
            "image": edited
        })
        
        # Build critique trace Markdown
        trace_md = "### Critique Trace\n"
        for h in state["history"]:
            v = h["verdict"]
            trace_md += f"**Iteration {h['iter'] + 1}**:\n"
            trace_md += f"- Score: `{v.get('score', 0)}/10`\n"
            trace_md += f"- Tone Match: {'✅' if v.get('tone_match') else '❌'}\n"
            trace_md += f"- Content Preserved: {'✅' if v.get('content_preserved') else '❌'}\n"
            if v.get("content_changes") and v.get("content_changes") != "none":
                trace_md += f"  - Changes: *{v.get('content_changes')}*\n"
            trace_md += f"- Biggest Gap: *{v.get('biggest_gap', 'None')}*\n"
            trace_md += f"- Fix: *{v.get('fix_instruction', 'None')}*\n\n"
            
        yield edited, state["iters"], spec, trace_md, state
        
        if verdict.get("score", 0) >= threshold:
            break
            
        if i < max_iters - 1:
            try:
                prompt = agent.refine_prompt(prompt, verdict, spec)
                state["last_prompt"] = prompt
                current_fix_instruction = verdict.get("fix_instruction")
            except Exception as e:
                raise gr.Error(f"Refining prompt failed: {e}")

@ui_callback
def apply_feedback(feedback_text, state):
    if not state or not state.get("result"):
        raise gr.Error("No active session. Please edit a photo first.")
    if not feedback_text:
        raise gr.Error("Please enter feedback before applying.")
        
    source_img = state["source"]
    spec = state["spec"]
    last_prompt = state["last_prompt"]
    history = state["history"]
    iters = state["iters"]
    
    # Setup dummy critique verdict for the feedback
    feedback_verdict = {
        "score": 5,
        "tone_match": False,
        "content_preserved": True,
        "content_changes": "none",
        "biggest_gap": feedback_text,
        "fix_instruction": feedback_text
    }
    
    try:
        new_prompt = agent.refine_prompt(last_prompt, feedback_verdict, spec)
        new_prompt_for_edit = new_prompt + f"\n\nCRITIC FIX REQUIREMENT: {feedback_text}"
        state["last_prompt"] = new_prompt
        
        edited = agent.generative_edit(source_img, None, new_prompt_for_edit)
        iters.append(edited)
        state["result"] = edited
        
        verdict = agent.critique(edited, spec, source_img)
        history.append({
            "iter": len(history),
            "prompt": new_prompt_for_edit,
            "verdict": verdict,
            "image": edited
        })
    except Exception as e:
        raise gr.Error(f"Feedback execution failed: {e}")
        
    # Build critique trace Markdown
    trace_md = "### Critique Trace\n"
    for h in history:
        v = h["verdict"]
        it_label = f"Iteration {h['iter'] + 1}"
        trace_md += f"**{it_label}**:\n"
        trace_md += f"- Score: `{v.get('score', 0)}/10`\n"
        trace_md += f"- Tone Match: {'✅' if v.get('tone_match') else '❌'}\n"
        trace_md += f"- Content Preserved: {'✅' if v.get('content_preserved') else '❌'}\n"
        if v.get("content_changes") and v.get("content_changes") != "none":
            trace_md += f"  - Changes: *{v.get('content_changes')}*\n"
        trace_md += f"- Biggest Gap: *{v.get('biggest_gap', 'None')}*\n"
        trace_md += f"- Fix: *{v.get('fix_instruction', 'None')}*\n\n"
        
    return edited, iters, trace_md, state

# Layout construction
with gr.Blocks(title="Style-Match Photo Editor") as demo:
    state_val = gr.State(None)
    
    gr.Markdown("# Style-Match Photo Editor")
    gr.Markdown("Re-grade your photograph to match a target style, ensuring composition and content remain unchanged.")
    
    with gr.Row():
        with gr.Column():
            source_input = gr.Image(label="Source Photo", type="pil")
            ref_input = gr.Image(label="Style Reference Image (Optional)", type="pil")
            instruction_input = gr.Textbox(
                label="Text Instruction (Optional)",
                placeholder="e.g. moody black and white, faded blacks"
            )
            
            with gr.Row():
                max_iters_slider = gr.Slider(
                    minimum=1, maximum=5, value=5, step=1,
                    label="Max Iterations"
                )
                threshold_slider = gr.Slider(
                    minimum=1, maximum=10, value=8, step=1,
                    label="Quality Threshold"
                )
                
            edit_btn = gr.Button("Edit Photo", variant="primary")
            
        with gr.Column():
            result_output = gr.Image(label="Result", type="pil", interactive=False)
            
            with gr.Row():
                download_jpg_btn = gr.DownloadButton("Download result (JPG)")
                download_zip_btn = gr.DownloadButton("Download all iterations (ZIP)")
                
            critique_trace = gr.Markdown("### Critique Trace\nNo edits performed yet.")
            
            with gr.Accordion("Style Spec & Iterations", open=False):
                spec_output = gr.JSON(label="Style Spec (JSON)")
                gallery = gr.Gallery(
                    label="Iterations",
                    columns=3,
                    object_fit="contain",
                    allow_preview=True
                )
                
            feedback_input = gr.Textbox(
                label="Manual Refinement Feedback",
                placeholder="e.g. make the shadows slightly cooler..."
            )
            feedback_btn = gr.Button("Apply Feedback")
            
    # Bind edit photo button
    edit_btn.click(
        fn=do_run,
        inputs=[source_input, ref_input, instruction_input, max_iters_slider, threshold_slider, state_val],
        outputs=[result_output, gallery, spec_output, critique_trace, state_val]
    )
    
    # Bind apply feedback button
    feedback_btn.click(
        fn=apply_feedback,
        inputs=[feedback_input, state_val],
        outputs=[result_output, gallery, critique_trace, state_val]
    )
    
    # Bind download buttons
    download_jpg_btn.click(
        fn=generate_jpg,
        inputs=[state_val],
        outputs=[download_jpg_btn]
    )
    download_zip_btn.click(
        fn=generate_zip,
        inputs=[state_val],
        outputs=[download_zip_btn]
    )

if __name__ == "__main__":
    demo.launch(show_error=True)
