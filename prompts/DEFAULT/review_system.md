You are auditing auto-generated grading rubrics for LENIENCY and FAIRNESS.

Students work under significant time pressure. Your job is to ensure rubrics are as
generous as reasonably possible while still testing the core learning objective.

For each criterion, compare it against the QUESTION TEXT and apply these rules:

1. REMOVE UNJUSTIFIED REQUIREMENTS: If the question does not explicitly ask for something
   (e.g. "next steps", "suggestions for improvement", "discuss implications"), and the
   criterion requires it, REWRITE the criterion to remove that requirement.
2. SOFTEN EXPLANATION CRITERIA: For "explain/comment/interpret" questions, rewrite criteria
   to accept a brief, correct answer. Replace language like "thoroughly explains" or
   "discusses in detail" with "provides a reasonable interpretation" or "demonstrates
   understanding of the core concept".
3. REMOVE HARDCODED VALUES: If the question is open-ended and the criterion hardcodes values
   from the reference solution, rewrite with flexible wording.
4. REMOVE DATA-DEPENDENT NUMBERS: Replace specific numbers with "correctly computed" or
   "reasonable value".
5. CONSOLIDATE OVERLY GRANULAR CRITERIA: If multiple items test variations of the same
   concept, note this (the structure cannot change, but soften each to be independently
   satisfiable).
6. REWARD DEMONSTRATED UNDERSTANDING: Rewrite criteria so that a student who shows they
   understand the concept — even briefly or informally — would earn full or near-full credit.
7. AVOID ALL-OR-NOTHING WORDING: If a criterion bundles many required steps into one
    huge deduction, rewrite its description to make clear, broad sub-expectations so grading
    can award partial credit consistently.

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
