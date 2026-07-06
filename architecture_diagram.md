# Architecture Diagram - Style-Match Photo Editor

This document outlines the architecture, data flow, and components of the Style-Match Photo Editor application.

## Component Flow Diagram

The flowchart below displays how user inputs are processed, how the agent routes tasks, how the generative loop critiques and refines prompts (with fix instructions), and how outputs are exported.

```mermaid
graph TD
    %% Nodes styling
    classDef ui fill:#e3f2fd,stroke:#1565c0,stroke-width:2px,color:#000000;
    classDef agent fill:#f3e5f5,stroke:#6a1b9a,stroke-width:2px,color:#000000;
    classDef ext fill:#fff3e0,stroke:#e65100,stroke-width:2px,color:#000000;
    classDef decision fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px,color:#000000;

    subgraph UI ["app.py (Gradio UI Blocks)"]
        Inputs["Inputs<br>- Source Image<br>- Ref Image (Opt)<br>- Instruction (Opt)<br>- Sliders (Iters/Thresh)"]:::ui
        Display["Display Components<br>- Final Result<br>- StyleSpec JSON<br>- Critique Trace (MD)<br>- Iterations Gallery"]:::ui
        Downloads["Download Actions<br>- Full-res JPEG<br>- Iterations ZIP"]:::ui
        FeedbackBox["Manual Feedback Box<br>- Apply feedback button"]:::ui
    end

    subgraph Agent ["agent.py (Core Logic)"]
        Perceive["perceive()<br>Analyze reference and instructions<br>(Generates StyleSpec JSON)"]:::agent
        Router{"plan()<br>Route based on keywords & reference"}:::decision
        DetEdit["deterministic_edit()<br>Fast B&W, Sepia, Contrast<br>(Pillow processing)"]:::agent
        
        subgraph GenLoop ["Generative Loop (Up to 5 Iterations)"]
            PromptBuilder["build_edit_prompt()<br>Formats base prompt"]:::agent
            GenEdit["generative_edit()<br>Grades source photo tone<br>(Excludes reference image)"]:::agent
            Critic["critique()<br>QA comparison vs original source<br>(Capped <=4 if content changes)"]:::agent
            Refine["refine_prompt()<br>Reformulates prompt from verdict"]:::agent
            FixAppend["Fix Injection<br>Appends 'CRITIC FIX REQUIREMENT'"]:::agent
        end
    end

    subgraph External ["Gemini API Models"]
        ThinkModel["gemini-3.5-flash<br>(Vision & Reasoning)"]:::ext
        ImageModel["gemini-3.1-flash-image<br>(Tonal Colorist Pass)"]:::ext
    end

    %% Routing and Execution flow
    Inputs -->|1. Run Edit| Perceive
    Perceive -->|Call| ThinkModel
    ThinkModel -->|Return StyleSpec| Perceive
    Perceive -->|StyleSpec JSON| Router
    
    Router -->|Deterministic| DetEdit
    DetEdit -->|2a. Return Same-Size Image| Display
    
    Router -->|Generative| PromptBuilder
    PromptBuilder -->|Base Edit Prompt| GenEdit
    
    %% Generative loop flow
    GenEdit -->|Source Image + Prompt| ImageModel
    ImageModel -->|Generated PIL Image| GenEdit
    GenEdit -->|Yield Image| Display
    GenEdit -->|Edited Image| Critic
    
    Critic -->|Original + Edited + Spec| ThinkModel
    ThinkModel -->|Return Verdict JSON| Critic
    
    Critic -->|Score < Threshold| Refine
    Critic -->|Score >= Threshold| Display
    
    Refine -->|Prev Prompt + Verdict| ThinkModel
    ThinkModel -->|Return Refined Prompt| Refine
    
    Refine -->|Refined Prompt| FixAppend
    Critic -->|Pass fix_instruction| FixAppend
    FixAppend -->|Prompt + Fix| GenEdit
    
    %% Human loop flow
    FeedbackBox -->|2b. Manual Critique| Refine
    
    %% Export Flow
    Display --> Downloads
```

## Architecture Details

### 1. Separation of Concerns (`agent.py` vs `app.py`)
- **`agent.py`** is UI-free and entirely testable headlessly. It handles the parsing, client construction, routing, edits, critique scoring invariants, and cli invocation flags.
- **`app.py`** imports `agent.py` and implements the Gradio Blocks page wrapper. It manages session variables cleanly in memory using `gr.State`.

### 2. Guardrails Against Content Changes
- **Reference Image Isolation**: The reference image is used solely to construct the text `StyleSpec` in the `perceive` phase. It is **never** sent to `gemini-3.1-flash-image` during editing, preventing compositional leaks (like background clouds, scenery elements, or smoothing effects).
- **Critic Verification**: The critic compares the graded result against the **original source photo** (rather than the reference) to catch any structural modifications. If it spots a change, the loop score is capped at $\leq 4$.

### 3. Iterative Critique Fix Injections
1. **Base Edit**: The prompt is built from the `StyleSpec` using `EDIT_TEMPLATE`.
2. **Analysis**: The critic outputs a `Verdict` detailing the score, content changes, biggest gap, and a concrete `fix_instruction`.
3. **Refinement**: If the score is below the threshold, `refine_prompt` uses `gemini-3.5-flash` to rewrite the prompt.
4. **Appendment**: To guarantee the instruction is prioritized, the prompt for the next iteration is formatted as:
   ```text
   [Refined Prompt]
   
   CRITIC FIX REQUIREMENT: [Verdicts fix_instruction]
   ```
5. **Human Input Integration**: Feedback typed by the user is passed in as a dummy critique verdict, causing the next loop iteration to inject it under `CRITIC FIX REQUIREMENT`.
