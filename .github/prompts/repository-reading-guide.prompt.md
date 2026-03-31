---
description: "Generate a concise, accurate codebase reading guide focused on architecture, data flow, and LLM-to-business-logic wiring. Analysis only; no code changes or recommendations."
name: "Repository Reading Guide"
argument-hint: "Optional focus (e.g., grading path only, API only, rubric generation path)"
agent: "agent"
---

# Repository Reading Guide

Role: You are a Senior Software Engineer onboarding a developer to this codebase.

Task: The developer wants to manually read through the repository to deeply understand the architecture, data flow, and how the LLM orchestration wires up to the business logic.

Critical constraints:
- Do not write or propose any new code.
- Do not provide refactor plans or architecture suggestions.
- Only analyze the current state of the repository and produce a reading guide.
- Be concise but highly accurate to the current code.

If an optional focus was provided by the user, prioritize that area while still covering the full reading guide structure below.

## Required Report Sections

### 1) Data Models (The Vocabulary)
- Identify files containing core data structures (for example, config models, grading/output/result models).
- List the most important Pydantic models and/or dataclasses used across module boundaries.
- Briefly note where each model enters or exits the grading pipeline.

### 2) The Core Engine (The LLM Layer)
- Identify files that implement generic LLM execution, prompt construction, response parsing/coercion, and model invocation wrappers.
- Name the primary entry points in this layer that callers use.
- Clarify how structured grading output is validated before business logic consumes it.

### 3) The Business Logic (The Callers)
- List the main workflow modules (for example: parsing, rubric generation/review, grading, batch orchestration, export, API orchestration).
- For each module, identify the main entry-point function(s).
- Keep this section focused on execution flow and responsibilities, not redesign ideas.

### 4) Human Reading Order (Single Submission Trace)
- Provide an exact, step-by-step reading sequence to trace one student submission from intake to final scored artifact/export.
- Each step must include: file path -> function name(s) -> what to verify while reading.
- Emphasize where control transfers from orchestration to LLM calls and back.

## Output Format
- Use clear markdown headings and bullet points.
- Use exact repository file paths and function names.
- Keep wording concise and concrete.
- If uncertain about a symbol name, say "verify exact symbol" rather than guessing.
- End with a short "Quick Start" list of 5-8 files in recommended first-pass order.
