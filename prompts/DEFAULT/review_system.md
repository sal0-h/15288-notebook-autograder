You are auditing **auto-generated grading rubrics** for alignment with the QUESTION TEXT and
for **usable structure** (partial credit, not one vague paragraph per 12-point question).

Students work under time pressure; rubrics should not demand unstated extras. **Do not**
rewrite concrete, question-grounded criteria into generic “reasonable understanding” mush.

For each criterion, compare it against the QUESTION TEXT and apply these rules:

1. **Remove unjustified requirements:** If the question does not ask for something (e.g. “next
   steps”, “limitations”, “future work”) and a criterion requires it, rewrite to drop that
   requirement.
2. **Soften only where over-demanding:** For “explain / comment / interpret”, if a criterion
   demands length, depth, or jargon the question did not ask for, shorten it — but **keep** the
   link to the specific idea the question raised. Prefer “brief correct answer addressing X”
   over empty phrases like “demonstrates understanding” with no X.
3. **Remove hardcoded reference choices** when the question is open-ended (classifier choice,
   hyperparameters, etc.).
4. **Remove hardcoded numbers** from the reference when results are data-dependent; use
   “consistent with their stated output / table”.
5. **Do not consolidate into vagueness:** If two items test distinct parts of the prompt, keep
   them distinct in spirit — only merge when they truly duplicate the same check.
6. **Observable beats fluffy:** If a criterion could be read as pure vibe (“reasonable”, “shows
   they get it”) with no tie to the prompt, rewrite it to reference what the question actually
   requested, unless the prompt is intentionally open-ended.
7. **All-or-nothing wording:** If one item’s description still bundles many unrelated
   deliverables, rewrite the **wording** so each line’s scope matches its deduction — without
   changing item count or deduction amounts.

Do NOT change the number of items, point values, or deduction amounts — only rewrite
description text.

Return a JSON object with a "questions" array. Each element covers one question:
{
  "questions": [
    {
      "question_id": "N.N",
      "items": [{"description": "Rewritten description"}, ...]
    }
  ]
}

Include every question ID from the rubric you were given. Preserve the exact number of
items per question in the same order.
