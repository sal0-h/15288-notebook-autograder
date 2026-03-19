# AI Autograder: Design Review & Refactoring Playbook

This document serves two purposes:

1. **Design review (Sections A–F)** — Evaluates the autograder as an AI-assisted assessment system: effectiveness, accuracy bottlenecks, roadmap, KPIs, and risks. It is not a code bug audit. Use it when changing prompts, rubrics, review policy, or arguing for product direction.
2. **Refactoring playbook** — Practical steps for understanding and changing code without breaking grading behavior. Use it before structural edits.

Implementation details, module map, and data formats live in [`CODEBASE_GUIDE.md`](./CODEBASE_GUIDE.md). Start with `README.md` and that guide when onboarding to the codebase.

**Scope of the system under discussion**

- Gather submissions
- Parse notebooks
- Generate rubrics from reference solutions
- Grade with LLM prompts and structured JSON
- Calibrate and review results
- Export Gradescope-compatible outputs

**Design invariants** (both review and refactors should respect these)

- Student-content prompt injection protections
- Canonical numeric question IDs
- Partial regrading and merge support
- Persisted assignment-scoped grading artifacts
- Backward-compatible export payloads

## Contents

- [Section A: Overall effectiveness](#section-a-overall-effectiveness-assessment)
- [Section B: Main accuracy bottlenecks](#section-b-main-accuracy-bottlenecks)
- [Section C: Highest-impact improvements](#section-c-highest-impact-improvements)
- [Section D: Short-term vs long-term roadmap](#section-d-short-term-vs-long-term-roadmap)
- [Section E: Evaluation plan and KPIs](#section-e-evaluation-plan-and-kpis)
- [Section F: Risks, tradeoffs, open questions](#section-f-risks-tradeoffs-and-open-questions)
- [Final recommendation](#final-recommendation)
- [Refactoring playbook](#refactoring-playbook)

---

## Section A: Overall Effectiveness Assessment

### Overall judgment

The architecture is stronger than a typical one-shot LLM grader and is viable as an AI-assisted grading pipeline for Jupyter notebook assignments. It has the right high-level decomposition for real operation: parsing, rubric generation, grouped grading, structured validation, calibration, export, and resumable assignment-scoped artifacts.

That said, the system should currently be treated as a high-throughput assistant to human graders, not as a fully trusted autonomous final grader. The strongest parts of the design are operational rather than epistemic:
- It is reproducible enough to rerun and inspect.
- It is robust enough to survive malformed model output.
- It is structured enough to support partial regrading and post-hoc review.

The main limitation is that grading correctness still depends on three upstream assumptions being correct at the same time:
- The parser must attach the right evidence to the right question.
- The rubric must reflect what the question asked rather than how the reference happened to solve it.
- The grading prompt must expose enough grounded evidence for the model to evaluate alternate correct answers fairly.

If any of those assumptions fail, the downstream pipeline largely preserves the mistake rather than repairing it.

### Where this architecture is likely to work well

- Notebook assignments with strongly templated question headers and stable cell ordering.
- Questions with relatively local evidence, such as code snippets, short outputs, or concise written responses.
- Courses where staff are willing to review flagged cases and periodically spot-check results.
- Environments where resumability, auditability, and partial reruns matter operationally.

### Where it is likely to struggle

- Open-ended or interpretation-heavy questions.
- Assignments where alternate correct implementations are common.
- Notebooks with cell reordering, duplicated labels, extra scratch work, or answers split across unusual cells.
- Plot- or output-heavy questions where evidence may be truncated or visually ambiguous.
- Large-scale grading runs without a targeted human review queue.

### Bottom line

This is a good production skeleton for AI-assisted notebook grading, but not yet a high-trust autonomous grader. Accuracy is more likely being limited by evidence capture and rubric design than by JSON validation, retry logic, or export mechanics.

## Section B: Main Accuracy Bottlenecks

### 1. Notebook parsing is the largest upstream accuracy risk

Why it matters:
Notebook grading is only as good as the evidence attached to each question. Unlike plain-text answers, notebook answers are often split across markdown, code, printed output, plots, and cells that do not strictly follow the original template.

Likely failure modes:
- Correct work is present but attached to the wrong question.
- Correct work exists outside the expected question boundary and is treated as missing.
- Duplicate or malformed question labels cause the latest occurrence to overwrite earlier evidence.
- Missing output cells are interpreted as conceptual failure when the student code itself is sufficient evidence.

Expected effect on grading:
- High false-negative risk.
- Fairness degradation for students who deviate from the notebook template.
- Lower trust in downstream calibration because the wrong evidence is being calibrated.

### 2. Rubric generation remains reference-shaped even with a fairness review pass

Why it matters:
The system already includes a rubric review step to soften solution-specific wording, which is a good design choice. But when the rubric is still derived from one reference solution, it remains vulnerable to overfitting around the instructor's decomposition of the task.

Likely failure modes:
- Penalizing alternate correct methods.
- Requiring specific intermediate steps or outputs not actually demanded by the question.
- Unstable partial credit when the model is forced to infer broad criteria from a narrow reference.

Expected effect on grading:
- More harsh grading on students with valid but noncanonical solutions.
- Higher variance on open-ended questions.

### 3. Prompt construction and grounding are adequate but not yet strong enough for high-stakes grading

Why it matters:
Grouped grading is cost-efficient, but it increases the chance that evidence for adjacent questions bleeds together. Prompt truncation and image budgeting can also silently remove the very evidence needed for a fair judgment.

Likely failure modes:
- One question benefits from nearby correct work on another question.
- A student's crucial evidence is truncated while the model still returns a confident score.
- The model infers correctness from plausible-looking structure rather than question-specific evidence.

Expected effect on grading:
- Both false positives and false negatives.
- Reduced auditability because staff cannot easily tell whether a bad score came from bad reasoning or incomplete prompt context.

### 4. LLM output quality is structurally validated but semantically under-constrained

Why it matters:
The pipeline does a good job preventing malformed outputs from contaminating the result set. However, a valid JSON response can still encode a wrong grade, a misread rubric, or an unjustified confidence level.

Likely failure modes:
- Coherent but incorrect partial credit decisions.
- Over-crediting plausible reasoning without enough evidence.
- Under-crediting brief but correct explanations.
- Miscalibrated self-reported confidence.

Expected effect on grading:
- Hidden semantic errors that pass validation and are never escalated.

### 5. Calibration is currently too narrow if it relies mainly on score outliers

Why it matters:
Outlier detection catches unusual scores, but many important grading mistakes are statistically normal. A systematically biased rubric or parse failure can affect many students without creating z-score anomalies.

Likely failure modes:
- Incorrect grades that look ordinary compared with peers.
- Systematic under-crediting of alternate valid approaches.
- Review queues dominated by distribution artifacts rather than true grading risk.

Expected effect on grading:
- Low recall for meaningful mistakes.
- Staff time spent on low-yield reviews.

### 6. Partial grading and merge semantics are operationally strong but introduce consistency risk

Why it matters:
Partial regrading is highly valuable in production. The risk is not the merge mechanic itself; it is that later reruns may implicitly use different prompts, rubric interpretations, or models while untouched questions preserve earlier standards.

Likely failure modes:
- Mixed policy within the same assignment.
- Students regraded under a different effective rubric than their peers.
- Hard-to-explain score changes after reruns.

Expected effect on grading:
- Fairness concerns across time.
- Reduced trust during grade disputes.

### 7. Schema validation and export are important, but they are not the main places accuracy is lost

Schema validation is valuable for syntax reliability, and the export layer is important for operational correctness. Neither is likely to be the dominant cause of grading error. Their main trust impact is whether they preserve sufficient provenance and explanation for audit and review.

## Section C: Highest-Impact Improvements

The table below prioritizes the highest-leverage improvements for grading accuracy and trust.

| Priority | Recommendation | Why It Matters | Failure Mode Addressed | Expected Accuracy Impact | Implementation Difficulty | Risks / Tradeoffs |
|---|---|---|---|---|---|---|
| 1 | Add parse-audit artifacts before grading | Parsing errors poison every downstream stage | Missing answers, misattached cells, duplicate label confusion | Very high | Medium | More preprocessing artifacts and UI/reporting work |
| 2 | Require staff-reviewed, variant-tolerant rubrics | Reference-derived rubrics remain too narrow | Alternate correct answers penalized, unstable partial credit | Very high | Medium | More staff setup time |
| 3 | Split grading into evidence extraction then scoring | Forces grounded reasoning before scoring | Hallucinated or weakly grounded grades | High | Medium | More latency and token cost |
| 4 | Use multi-signal review triggers instead of relying on model confidence or outliers | Wrong grades often look syntactically valid and statistically ordinary | Missed review cases, low-yield review queues | High | Medium | Larger queue until tuned |
| 5 | Add per-question provenance in persisted results | Needed for explainability and fair regrading | Mixed standards across reruns and model changes | Medium for accuracy, high for trust | Low-Medium | Larger result payloads |
| 6 | Add selective adjudication or second-pass grading for high-risk questions | Hard questions deserve more than one weak signal | Instability on open-ended, plot-heavy, or ambiguous questions | Medium-High | Medium | Higher inference cost |
| 7 | Redesign calibration around review yield, not only z-scores | True grading mistakes are not always outliers | Weak review ranking and missed systematic errors | Medium | Medium | Needs reviewed data to tune |
| 8 | Build assignment- and question-type-specific prompt variants | Notebook grading needs different handling by evidence type | One generic prompt fits none of code, prose, numeric, and plot questions equally well | Medium | Medium | Risk of prompt sprawl without benchmark discipline |

### Recommendation details

#### 1. Add parse-audit artifacts before grading

What to add:
- Per-student parse summary showing detected question IDs, duplicate labels, and question-to-cell attachment ranges.
- Flags for questions with no code, no markdown, no outputs, or suspicious orphaned cells between recognized questions.
- A staff-facing parse preview for a sample of notebooks and all anomalous notebooks.

Why it matters:
This catches the most damaging grading failures before LLM judgment begins.

Failure mode addressed:
False negatives caused by extraction errors rather than student misunderstanding.

Expected impact on grading accuracy:
Very high, especially on messy or non-template-conforming submissions.

Implementation difficulty:
Medium.

Risks and tradeoffs:
- More reporting artifacts.
- Slightly more operational complexity.

#### 2. Require staff-reviewed, variant-tolerant rubrics

What to add:
- Accepted-variant notes for each question.
- Explicit statements of what should not be penalized.
- Partial-credit anchors tied to missing evidence rather than reference steps.

Why it matters:
This is the single strongest fairness control after parsing.

Failure mode addressed:
Overfitting to the instructor's solution and penalizing alternative correct approaches.

Expected impact on grading accuracy:
Very high.

Implementation difficulty:
Medium.

Risks and tradeoffs:
- Requires some staff time per assignment.
- May reduce automation speed upfront while improving downstream consistency.

#### 3. Split grading into evidence extraction then scoring

What to add:
- Stage 1: extract question-specific evidence from the parsed notebook.
- Stage 2: score only against the extracted evidence and rubric.
- Review trigger when extracted evidence is sparse, contradictory, or ambiguous.

Why it matters:
It makes the grading decision more auditable and less likely to be based on inference from irrelevant context.

Failure mode addressed:
Plausible but weakly grounded scoring, especially on grouped prompts.

Expected impact on grading accuracy:
High.

Implementation difficulty:
Medium.

Risks and tradeoffs:
- Higher latency and token use.
- More orchestration complexity.

#### 4. Use multi-signal review triggers

What to add:
- Trigger review for parse anomalies.
- Trigger review for prompt truncation.
- Trigger review for large deductions with sparse evidence.
- Trigger review when scores disagree across two grading passes or when justification is weak.
- Trigger review when output-dependent questions lack outputs.

Why it matters:
Model confidence is not a strong enough control signal on its own.

Failure mode addressed:
Wrong but well-formed grades that otherwise look normal.

Expected impact on grading accuracy:
High.

Implementation difficulty:
Medium.

Risks and tradeoffs:
- Review volume may increase until thresholds are tuned.

#### 5. Add per-question provenance

What to add:
- Model name
- Prompt hash or version
- Rubric version
- Parse version
- Timestamp
- Whether the score came from a merge or a fresh run

Why it matters:
Without provenance, partial regrading weakens consistency and makes disputes hard to resolve.

Failure mode addressed:
Mixed grading standards across time.

Expected impact on grading accuracy:
Moderate.

Implementation difficulty:
Low to medium.

Risks and tradeoffs:
- Slightly larger stored results.

#### 6. Use selective adjudication for high-risk questions

What to add:
- A second grading pass only for flagged questions.
- Or an independent critique pass that can revise a score only with explicit evidence.

Why it matters:
Some question types are too ambiguous for a single pass to be trusted.

Failure mode addressed:
Instability on open-ended, plot-heavy, or alternate-solution questions.

Expected impact on grading accuracy:
Medium to high.

Implementation difficulty:
Medium.

Risks and tradeoffs:
- Higher inference cost.
- More complexity in the review queue.

#### 7. Redesign calibration around review yield

What to add:
- Rank cases by estimated error risk instead of or in addition to z-score magnitude.
- Use historical human overrides to tune which signals actually predict wrong grades.

Why it matters:
The review queue should catch likely mistakes, not just unusual scores.

Failure mode addressed:
Low-value review workload and missed systematic errors.

Expected impact on grading accuracy:
Medium.

Implementation difficulty:
Medium.

Risks and tradeoffs:
- Requires labeled review outcomes to tune well.

#### 8. Create question-type-specific prompts

What to add:
- Separate prompt strategies for code implementation, explanation, numeric answers, and plot interpretation.
- Explicit instructions on how to handle missing outputs versus missing reasoning.

Why it matters:
Notebook grading is multimodal, and one generic prompt does not reliably serve all cases.

Failure mode addressed:
Weak handling of plots, concise prose, and alternate code implementations.

Expected impact on grading accuracy:
Medium.

Implementation difficulty:
Medium.

Risks and tradeoffs:
- More prompt surface area to maintain.
- Risk of prompt drift unless benchmarked.

## Section D: Short-Term vs Long-Term Roadmap

### Short-term roadmap

These changes can improve trust and grading quality without re-architecting the pipeline:

1. Add parse-audit summaries and anomaly flags.
2. Require rubric review with explicit accepted variants and non-penalized differences.
3. Log prompt truncation and expose it as a review trigger.
4. Add per-question provenance to persisted grading results.
5. Replace pure outlier review with a ranked queue using parse and prompt signals.
6. Run stratified staff audits across questions and score bands before final export.

### Long-term roadmap

These changes materially raise the accuracy ceiling:

1. Move to two-stage evidence extraction and scoring.
2. Build a gold-standard benchmark set with adjudicated human labels.
3. Tune prompts and review triggers against measured benchmark performance rather than intuition.
4. Use selective consensus grading for high-risk question types.
5. Move toward hybrid grading where deterministic checks are used when available and LLM judgment is reserved for genuinely interpretive work.

## Section E: Evaluation Plan and KPIs

### Core evaluation principle

Accuracy should not be judged by whether outputs look reasonable to staff on a few examples. It should be measured against a frozen, adjudicated benchmark set of question-level grading examples.

### Benchmark dataset to build

Build a gold-standard evaluation set containing:
- Full-credit, partial-credit, and zero-credit examples.
- Borderline cases that produce staff disagreement.
- Alternate correct implementations.
- Brief but correct explanations.
- Missing-output notebooks where code still demonstrates understanding.
- Plot-heavy questions.
- Parsing edge cases such as duplicate labels, extra scratch cells, and misplaced answers.
- Attempted prompt-injection content inside notebooks.

Recommended process:
- Sample question-level examples, not just full notebooks.
- Use at least two independent human graders.
- Adjudicate disagreements into a final gold label.
- Preserve grader rationales for a subset of benchmark cases.

### KPIs

Primary KPIs:
- Exact-score agreement with adjudicated human grades.
- Mean absolute error in points.
- Severe error rate, such as being off by more than 20 percent of question points or more than 1 point.
- False positive rate, meaning overly generous grades on incorrect work.
- False negative rate, meaning overly harsh grades on correct or mostly correct work.

Review KPIs:
- Review precision: of cases sent to staff review, how many are actually changed.
- Review recall: of materially wrong grades, how many are captured by review triggers.
- Override rate by question type and trigger reason.

Stability KPIs:
- Inter-run consistency under the same config and model.
- Consistency across partial reruns.
- Calibration yield, meaning how many flagged cases are truly problematic.

Fairness KPIs:
- Error rate by question type.
- Error rate by notebook conformity to template.
- Error rate on alternate correct approaches.
- Error rate on concise versus long written answers.

### Experimental methodology

Use a fixed baseline and then change one major design variable at a time where practical:
- Rubric design
- Prompt design
- Review trigger policy
- Calibration ranking policy
- Evidence extraction strategy

Adopt changes only if they improve the benchmark on severe-error rate and false-negative rate without materially harming consistency or review precision.

## Section F: Risks, Tradeoffs, and Open Questions

### Likely false positive grading patterns

- Plausible prose earns too much credit even when reasoning is weak.
- Correct-looking outputs receive credit when the method is conceptually wrong.
- Nearby correct work in the same grouped prompt influences another question's score.
- Template-following structure is mistaken for conceptual understanding.

### Likely false negative grading patterns

- Correct work is missed because it sits outside the expected question boundary.
- Alternate valid solutions do not match the rubric's implicit reference shape.
- Brief but correct written answers are treated as incomplete.
- Missing outputs are penalized even when the code itself shows the intended method.

### Bias and unfairness risks

- Template-adherence bias: students who format exactly as expected are easier to grade correctly.
- Expression bias: concise or nonnative-English explanations may be graded more harshly on prose questions.
- Tooling bias: students using different libraries or coding styles may be penalized if rubrics remain reference-shaped.
- Rerun bias: partial regrades can expose different students to different effective grading standards.

### Where human-in-the-loop review should be inserted

- After parsing, for all anomalous notebooks and a staff sample of normal notebooks.
- After rubric generation, before the first full grading run.
- During grading, for multi-signal triggered cases.
- After calibration, using a ranked review queue rather than a pure outlier list.
- Before final export, through stratified spot checks across score bands and question types.

### Telemetry and artifacts that should be logged

- Parse coverage per question: code, markdown, output, images.
- Duplicate and missing question labels.
- Prompt token count and explicit truncation flags.
- Raw model output and normalized validated output.
- Retry counts and validation failure reasons.
- Per-question evidence summary used for grading.
- Review trigger reasons.
- Human override outcomes and rationale tags.
- Per-question provenance including model, prompt, rubric, and merge history.

### Open questions for deployment

- How frequently do real student notebooks deviate from the intended cell structure?
- Which question types currently generate the most staff overrides?
- Is the operational goal low-touch automation or high-trust staff-assisted grading?
- How often are prompts, models, and rubrics changed during an assignment's lifecycle?
- What level of grading inconsistency is acceptable before mandatory staff review is required?

## Final Recommendation

The current system is a solid foundation for AI-assisted notebook grading, but its trustworthiness will improve much more from better evidence capture, more variant-tolerant rubricing, and smarter review insertion than from additional syntax validation or generic prompt tuning.

If the next development cycle can only fund a few changes, the strongest sequence is:
1. Parse-audit artifacts and anomaly review.
2. Staff-reviewed, variant-tolerant rubrics.
3. Per-question provenance and multi-signal review triggers.
4. Benchmark-driven evaluation using adjudicated human grades.

That sequence directly targets the most likely sources of grading error while preserving the current pipeline's operational strengths.

---

## Refactoring playbook

Practical guide: what to read, what to run, and how to refactor without breaking grading behavior.

### 1. Start with a system map

Read files in this order:

1. `README.md`
2. `docs/CODEBASE_GUIDE.md`
3. **This document**, Sections A–B, when your change touches parsing, rubrics, grouping, or grading policy (effectiveness context); skip if you are doing a narrow mechanical refactor.
4. `main.py` (CLI entry, step dispatch)
5. `app.py` (API, locks, SSE)
6. `pipeline_runner.py` (thin stage wrappers for the web app)
7. `parse_notebook.py`
8. `rubric/` (`impl.py`)
9. `grade.py`
10. `batch_grader.py`
11. `export.py`
12. `gather.py` — if you touch submission ingest
13. `results_store.py` — if you touch persistence or resume
14. `prompt_builder.py` — if you touch prompts, sanitization, or token budgeting
15. `grading_models.py` — if you touch LLM response schema or feedback sentinels
16. `utils.py`, `grading_helpers.py`, `config_models.py`

As you read, write a short architecture note for yourself with:

- Main entry points: `app.py` (API), `main.py` (CLI)
- Pipeline flow: gather → parse → rubric → grade → (calibrate) → export
- Shared utility boundaries
- Where persistent files are read/written

### 2. Know the invariants before refactoring

Do not start edits until these are clear. They are easy to break accidentally:

1. Runtime config source of truth is `output/{assignment_name}/config.yaml`.
2. At `AppConfig | dict` boundaries use `ensure_app_config`; do not call `AppConfig.model_validate()` directly in pipeline code (see `CODEBASE_GUIDE.md`).
3. Prompts load from the filesystem (`prompts/{assignment}/` or `prompts/DEFAULT/`) and are not persisted inside config payloads.
4. Runtime artifacts are assignment-scoped under `output/{assignment_name}/`.
5. Question IDs are canonical numeric strings (`"1.1"`); normalize at LLM/UI boundaries — see `grading_helpers` / `GradingResponse.from_raw` / `normalize_qid`.
6. Totals and exports respect feedback sentinels (`SKIP_FEEDBACKS`, `NO_SUBMISSION`, etc. in `grading_models.py`).
7. `graded_results.json` is incrementally saved and supports resume behavior.
8. `grade_only` and `grade_only_merge` semantics must remain intact.
9. API payload shapes consumed by `ui/js/*.js` and tests must remain backward-compatible.
10. Tests must mock the OpenAI client — never hit the live API in `tests/` (see `CODEBASE_GUIDE.md` §9).

### 3. Build a baseline before you touch code

Use a clean baseline so regressions are obvious.

```bash
.venv/bin/python -m pytest tests/ -q
```

Focused commands:

```bash
# API behavior
.venv/bin/python -m pytest tests/test_app.py -q

# Pipeline behavior
.venv/bin/python -m pytest tests/test_parse.py tests/test_rubric.py tests/test_grade.py tests/test_export.py -q

# Shared helpers / config / persistence
.venv/bin/python -m pytest tests/test_utils.py tests/test_results_store.py -q
```

### 4. Pick one seam per refactor

Do not refactor the entire project in one pass. Pick one seam and finish it end-to-end.

Good seams in this repository:

1. Path resolution and assignment-scoped artifact paths
2. Config loading, merging, and validation boundaries
3. Results persistence and recovery (`results_store.py`)
4. Grade-only filtering logic (`grading_helpers.py`)
5. Logging setup and assignment binding
6. Prompt construction and sanitization (`prompt_builder.py`)
7. API stage dispatch (`pipeline_runner.py`, `app.py`)

A good seam is small enough to test thoroughly and review quickly.

### 5. Trace dependencies before editing

For each symbol you change, inspect call sites first:

```bash
rg -n "symbol_name" *.py tests -S
```

Before extracting helpers, answer:

1. Who calls this now?
2. Is behavior duplicated with subtle differences?
3. Which tests currently cover this behavior?

### 6. Refactor with tight feedback loops

Use this loop for each seam:

1. Add or update tests for intended behavior.
2. Make one focused change.
3. Run only relevant tests.
4. If green, run a wider suite.
5. Commit.

If you skip step 1, you will likely preserve the wrong behavior.

### 7. Prefer these refactor patterns

1. Replace repeated ad hoc logic with a shared helper.
2. Prefer typed models or dataclasses for core contracts over string-key dictionaries where it reduces ambiguity.
3. Keep module responsibilities single-purpose:
   - `parse_notebook.py`: parsing
   - `rubric/`: rubric generation (`impl.py`)
   - `grade.py`: per-student grading
   - `batch_grader.py`: orchestration / resume
   - `export.py`: export artifacts
4. Keep backward-compatible API response shapes.

### 8. Preserve safety and concurrency guarantees

When touching `app.py` or the grading flow:

1. Preserve lock usage:
   - `_grading_lock`
   - `_results_lock`
   - `_rubric_lock`
2. Keep lock ordering consistent to avoid deadlocks.
3. Treat student notebook content as untrusted prompt input (delimiter sanitization, path safety).
4. Preserve assignment-scoped logging (`output/{assignment_name}/autograder.log`).

### 9. Manual review checklist for every change

1. Does it change where config is read/written?
2. Does it alter output file locations?
3. Can it break resume behavior?
4. Can it break UI payload shape?
5. Can it break grade-only or merge semantics?
6. Are errors still actionable for users?
7. Are tests updated to reflect intended behavior?

### 10. Suggested refactor order (low risk → higher risk)

Start here:

1. Shared path and config helper cleanup
2. Logging consistency cleanup
3. Duplicate helper consolidation in `grading_helpers.py`
4. Results-store robustness and error paths

Then move to higher-risk areas:

1. `app.py` endpoint consolidation (shared validation / helpers)
2. `batch_grader.py` orchestration simplification
3. Surgical decomposition of `grade.py`

### 11. Example session plan (~90 minutes)

1. 10 min: choose seam and write an explicit goal
2. 15 min: trace call sites and existing tests
3. 20 min: write or adjust tests first
4. 25 min: implement minimal code change
5. 10 min: run focused tests
6. 10 min: run broader tests and write a short change note

Repeat this cycle. Consistency compounds over time.

### 12. Common mistakes to avoid

1. Refactoring style without understanding runtime invariants
2. Mixing multiple seams in one PR
3. Changing config payload shape without updating UI/tests
4. Moving prompt text into config persistence
5. Breaking assignment-scoped path or log behavior
6. Calling `AppConfig.model_validate` on already-typed config in hot paths

### 13. Done criteria for a refactor

A refactor is done only when:

1. Behavior is unchanged except for explicit intended improvements
2. Focused tests pass
3. Broader regression tests pass
4. The code path is simpler to explain than before
5. Future changes require fewer touch points