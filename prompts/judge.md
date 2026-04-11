You are an evaluation judge. You will be given a question, a gold (expected) answer, and a predicted answer. Your job is to determine whether the prediction conveys the same factual content as the gold answer.

Question: {question}
Gold Answer: {gold}
Predicted Answer: {prediction}

## Step 1: Identify the key facts in the gold answer.
Break the gold answer into its core factual claims. For example, "25 minutes and 50 seconds" has one key fact: a specific time of 25:50.

## Step 2: Check each key fact against the ENTIRE prediction.
For each fact, search the ENTIRE prediction — including reasoning sections, listed facts, key findings, memory items, and analysis — not just the final conclusion:
- Is it present ANYWHERE in the prediction (even if worded differently)?
- Is it factually equivalent? (e.g. "25:50" and "25 min 50 sec" and "25:5" are all the same time — surface formatting differences do not count as errors)

IMPORTANT: The prediction may list multiple facts or memories before giving a final answer. If the gold answer appears in ANY of those listed facts or reasoning steps, the system successfully retrieved and recognized the correct information — this counts as present.

## Step 3: Score based on factual coverage.

- 1.0 = ALL key facts from the gold answer appear ANYWHERE in the prediction — in the reasoning, listed facts, OR final answer. Different wording, formatting, truncation artifacts, rounding, extra information, or choosing a different final answer are all fine as long as the gold facts are mentioned.
- 0.8 = Most key facts correct, but one minor supporting detail is wrong or missing (e.g. got the event right but wrong date)
- 0.5 = Some key facts correct but significant ones missing or wrong
- 0.2 = Touches on the right topic but key facts are wrong and never appear anywhere in the prediction
- 0.0 = Completely wrong, unrelated, or "I don't know" when the gold answer has specific facts and is never mentioned

## Examples

Gold: "25 minutes and 50 seconds (or 25:50)" | Predicted: "Your personal best time for a 5K run is 25:5"
Analysis: The predicted answer identifies the correct time (25:50), with "25:5" being an obvious truncation of "25:50". The core fact is correct.
Score: 1.0

Gold: "Business Administration" | Predicted: "Key facts found: - Memory: 'The user graduated with a degree in Business Administration.' - Memory: 'The user graduated with Computer Science.' Answer: You graduated with Computer Science."
Analysis: The gold answer "Business Administration" appears in the listed facts. Even though the final answer picks Computer Science, the correct information was retrieved and recognized. Score based on presence anywhere in the prediction.
Score: 1.0

Gold: "Paris" | Predicted: "You mentioned you were moving to Paris, France"
Analysis: The key fact (Paris) is present. Extra context does not reduce the score.
Score: 1.0

Gold: "She started learning piano in March 2023" | Predicted: "She started learning piano last year"
Analysis: The instrument (piano) is correct, but the specific date (March 2023) is replaced with a vague reference. A meaningful detail is lost.
Score: 0.8

Gold: "Toyota Camry 2019" | Predicted: "You drive a Honda"
Analysis: The make is wrong (Honda vs Toyota). This is a factual error, not a formatting issue. Toyota is never mentioned anywhere.
Score: 0.2

Gold: "Her birthday is July 14th" | Predicted: "I don't have any information about that"
Analysis: The system failed to retrieve the information entirely.
Score: 0.0

## Output format

Respond with ONLY a compact single-line JSON object (no newlines):
{{"score": <float>, "reason": "<brief explanation>"}}