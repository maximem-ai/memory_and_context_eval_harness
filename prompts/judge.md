Label the generated answer as CORRECT or WRONG.

Question: {question}
Gold Answer: {gold}
Predicted Answer: {prediction}

## Rules

1. **PARTIAL CREDIT (1-of-N)**: If the gold answer is a list and the predicted answer includes AT LEAST ONE correct item from that list, mark CORRECT. Getting 1 out of 2, 2 out of 4, etc. is always acceptable. Only mark WRONG if NONE of the gold items appear.

2. **PARAPHRASES COUNT**: Same concept in different words is CORRECT. "Volunteering at a homeless shelter" = "shelter meal service". "Proud" = "fulfilled" = "accomplished". "Got hurt" = "injured" = "got an injury". Judge semantic meaning, not exact wording.

3. **EXTRA DETAIL IS FINE**: A longer answer that includes the gold's key fact plus additional information is CORRECT. Never penalize for being more detailed or specific.

4. **DATE TOLERANCE**: Dates within 14 days of each other are CORRECT. Durations within 50% are CORRECT (e.g. "5 months" matches "six months"; "19 days" matches "two weeks"). Off-by-one-day (Saturday vs Sunday for adjacent dates, or off-by-one weekend) is CORRECT.

5. **CONVERSATIONAL DATE MATH**: When a question asks about WHEN something happened and the conversation reference is a relative phrase, accept the predicted date if it correctly applies the math from the conversation. The rule is "subtract the relative offset from the session year/date":
   - "Last year" said in a session dated 2010 → 2009. "Last year" said in a session dated 2050 → 2049. Apply the same arithmetic to whatever the session year is.
   - "Last week" / "Last month" / "Last Saturday" said in a known session date → the predicted date that correctly counts back from the session date is CORRECT, even if the gold answer phrases it differently or chose a different anchor session.
   - "Next month" / "Next week" said in a known session date → the predicted date that correctly counts forward is CORRECT.
   - In short: if the predicted answer's date computation is consistent with what a human would derive from the conversation, accept it.

6. **RELATIVE-DATE WINDOWS — CRITICAL**: Gold answers may phrase a date as a window relative to a session date — for example "the week before <DATE>", "the weekend before <DATE>", "the Sunday before <DATE>", "two weekends before <DATE>". When the gold uses this form, compute the implied window and accept ANY predicted date that falls inside it — also accept dates within 7 days on either side of the window (anchor ambiguity tolerance).

   Concretely:
   - Gold "the week before <DATE>" → window = the 7 days immediately preceding <DATE>. Any predicted date inside that window OR within 7 days of either edge is CORRECT.
   - Gold "the weekend before <DATE>" → the Saturday/Sunday pair immediately before <DATE>. Any predicted date that names a Saturday or Sunday within 14 days of <DATE> is CORRECT.
   - Gold "the [Friday/Saturday/Sunday/...] before <DATE>" → the named weekday immediately before <DATE>. Any predicted date for that named weekday within 14 days of <DATE> is CORRECT. Off-by-one weekday (Saturday vs Sunday for adjacent dates) is also CORRECT (consistent with rule 4).
   - Gold "two weekends before <DATE>" / "the Sunday two weeks before <DATE>" → the appropriate weekend two weeks prior. Any predicted date within ±7 days of that target is CORRECT.
   - Gold "in <month>" → any specific date inside that month is CORRECT.
   - Gold gives a specific date and prediction phrases it as "the week of <RANGE>" / "around <DATE>" / "early/mid/late <month>" — if the named range or descriptor is consistent with the gold date (within 14 days), CORRECT.

   The model may use a different anchor session for its date math than the gold did. Anchor choice is a stylistic difference — only the resulting date window matters. Do NOT penalize a prediction for explaining its anchor when the resulting date is within tolerance of the gold window.

7. **MULTI-ANSWER QUESTIONS**: If the question's gold answer is something like "X" but the conversation contains multiple plausible answers (X, Y, Z) and the predicted answer is any of them and is supported by the retrieved evidence, mark CORRECT. Do not penalize the model for picking a different valid answer than the gold.

   - This includes the case where the prediction enumerates multiple candidate items (e.g., "X, Y, and Z") and one of them matches the gold — still CORRECT.

8. **COUNT ↔ ENUMERATION**: When the gold is a count (e.g. "3") and the prediction enumerates that many distinct items consistent with the question, mark CORRECT. When the gold is a list and the prediction gives a count matching the list length plus the right items, also CORRECT. Counting and enumerating are different surface forms of the same underlying fact.

9. **SEMANTIC OVERLAP**: Judge whether the predicted answer addresses the same topic and captures the core idea of the gold answer. Different wording, phrasing, or level of detail should not result in WRONG if the underlying concept matches.

10. **SAME REFERENT**: If the predicted answer mentions or references the same named entity, character, person, or concept as the gold answer, mark CORRECT — even if descriptions differ.

11. **FOCUS ON KNOWLEDGE, NOT WORDING**: The goal is to assess whether the system recalled the right fact. Minor differences in specificity, phrasing, or scope should not result in WRONG.

## ONLY mark WRONG if:
- The predicted answer contains ZERO correct items from the gold answer AND no plausible interpretation of the prediction matches the gold
- The answer addresses a completely different topic
- The predicted answer says "I don't know" / "not specified" / "no information" AND the gold answer has specific facts
- For relative-date-window gold answers (rule 6): the predicted date falls clearly outside the implied window AND outside the ±7-day tolerance

Respond with ONLY a compact single-line JSON object (no newlines):
{{"score": <float between 0.0 and 1.0>, "reason": "<one sentence>"}}

Use 1.0 for CORRECT and 0.0 for WRONG. Do NOT use partial scores like 0.5 or 0.8 — the judgment is binary.
