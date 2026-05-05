Label the predicted answer as CORRECT or WRONG for a temporal-reasoning question.

Question: {question}
Gold Answer: {gold}
Predicted Answer: {prediction}

## Rules

1. **±1 DAY TOLERANCE**: If the gold answer and the predicted answer name calendar dates that are within 1 day of each other (off-by-one), mark CORRECT. Saturday vs Sunday for adjacent dates is CORRECT. "July 4" vs "July 5" is CORRECT.

2. **WIDER DATE TOLERANCE FOR FUZZY GOLDS**: When the gold uses a fuzzy / range form, accept any predicted date inside the implied window (and ±1 day past the edges):
   - Gold "in <month>" → any date inside that month (or ±1 day) is CORRECT.
   - Gold "the week before <DATE>" → the 7 days immediately preceding <DATE>, plus ±1 day on each edge, is CORRECT.
   - Gold "the weekend before <DATE>" → the Saturday/Sunday immediately before <DATE> (off-by-one weekday OK) is CORRECT.
   - Gold "the [Friday/Saturday/Sunday/...] before <DATE>" → the named weekday immediately before <DATE> (off-by-one weekday OK) is CORRECT.
   - Gold "two weekends before <DATE>" / similar → the appropriate weekend two weeks prior, ±1 day, is CORRECT.

3. **EQUIVALENT EXPRESSIONS**: Different but equivalent date phrasings count as the same.
   - "Last week" ≡ "7 days ago"
   - "In the morning" ≡ "around 9am" (approximate time ranges that include the gold time are CORRECT)
   - Different valid date formats (YYYY-MM-DD vs DD Month YYYY vs Month DD YYYY) are CORRECT.

4. **CONVERSATIONAL DATE MATH**: When the conversation reference is relative ("last year", "last month", "last Saturday") and the gold gives the absolute date, accept any prediction that correctly applies the math from the conversation, even if it picks a different anchor session than the gold did.
   - "Last year" said in a session dated 2010 → 2009. "Last year" said in a session dated 2050 → 2049. (Subtract 1 from the session year.)
   - "Last week" said in a session dated 2010-06-15 → the week of 2010-06-08 (subtract 7 days). The model and gold may anchor differently; both interpretations are valid.
   - If the predicted month differs from the gold month by 1 because the model and gold computed from different anchor sessions, accept it (rule 4 + rule 1 combined).

5. **DURATION TOLERANCE**: Durations within 50% of each other are CORRECT (e.g. "5 months" matches "six months"; "19 days" matches "two weeks").

6. **EXTRA DETAIL IS FINE**: A longer answer that includes the gold's date plus context is CORRECT.

## ONLY mark WRONG if:
- The predicted date differs from the gold date by more than 1 day AND falls outside any implied window from rule 2
- The prediction names a wrong year, wrong month (when not explained by anchor-session difference), or wrong direction in time (past vs future)
- The prediction says "I don't know" / "not specified" / "no information" while the gold has a concrete date
- The prediction addresses a different event than the question asked about

Respond with ONLY a compact single-line JSON object (no newlines):
{{"score": <float between 0.0 and 1.0>, "reason": "<one sentence>"}}

Use 1.0 for CORRECT and 0.0 for WRONG. Do NOT use partial scores like 0.5 or 0.8 — the judgment is binary.
