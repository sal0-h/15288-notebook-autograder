---
description: "Analyze and report changes in grading behavior after modifying prompts, rubrics, or models. Use to validate that changes improve or maintain accuracy without unintended side effects."
name: "Assignment Regression Analysis"
argument-hint: "Assignment name and list of changes made (e.g. LabTest_3_S26, updated rubric for Q2.3)"
agent: "agent"
---

# Assignment Regression Analysis

Analyze grading behavior changes after modifying assignment config, rubrics, or prompts.

## What to Provide

- **Assignment name**: e.g., `LabTest_3_S26`
- **Changes made**: Brief list of modifications (e.g., "updated Q2.3 rubric for clarity", "switched to gpt-5-mini")
- **Baseline reference**: Prior results or expected behavior if available

## Analysis

The agent will:

1. **Compare artifacts**:
   - Load the current `graded_results.json` and prior results if available.
   - Compare per-question score distributions, outlier flags, and special cases.
   - Identify questions with score shifts >= 5%.

2. **Check coverage**:
   - Verify all students/groups are graded.
   - Flag any new parse anomalies or truncation warnings.
   - Report on submission conformity to template.

3. **Spot-check evidence**:
   - Sample 5-10 borderline cases (partial credit, near-threshold scores).
   - Examine the evidence and justification against the updated rubric.
   - Flag any cases that look inconsistent with the rubric intent.

4. **Summarize deltas**:
   - List questions with material score changes.
   - Identify students whose grades moved significantly.
   - Note any new or resolved edge cases.

## Output Format

**Report Summary**:
```
Assignment: LabTest_3_S26
Changes: Updated Q2.3 rubric for clarity
Result: Overall mean score: 72.1 (was 71.8), std dev: 8.3 (was 8.1)

Material Changes (≥5%):
- Q1.2: +3.2% mean score (clearer grading boundary)
- Q2.3: +6.1% mean score (rubric clarification)

Flagged for Review (3 cases):
- student_101: Q2.3 shifted from 7/10 to 8/10 (check evidence)
- student_45: Q1.5 still looks harsh on alternate approach
- student_67: Parse anomaly in submission (extra cells)

Recommendation: Changes are safe. Spot-check the 3 flagged cases before finalizing grades.
```

## Further Steps

- **Accept**: Finalize and export results.
- **Revise**: Adjust rubric or prompt and rerun selective questions with `grade_only=["Q2.3"]`.
- **Audit**: Run manual spot checks on a larger sample before release.
