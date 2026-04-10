"""
Local test script for tuning the system prompt on single-session-preference questions.

Loads the 8 preference questions from the checkpoint (same fetched contexts as the best run),
runs the answer LLM with the current system prompt, and displays predictions vs gold answers.

Usage:
    python scripts/test_preference_prompt.py                          # uses prompts/system_prompt.md
    python scripts/test_preference_prompt.py --prompt path/to/alt.md  # test an alternative prompt
    python scripts/test_preference_prompt.py --model gpt-4o           # override model
    python scripts/test_preference_prompt.py --question 32260d93      # run only one question (partial ID match)
"""

import asyncio
import json
import os
import sys
import argparse
import re

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()


CHECKPOINT_PATH = "checkpoints/orchestrator/longmemeval.json"
DATASET_PATH = "datasets/longmemeval/longmemeval_109_answerable.json"
DEFAULT_PROMPT_PATH = "prompts/system_prompt.md"
DEFAULT_MODEL = "gpt-5-mini"


def build_ranked_context(item_objects):
    """Same logic as orchestrator._build_ranked_context"""
    if not item_objects:
        return ""
    parts = []
    for i, item in enumerate(item_objects):
        context_type = item.get("context_type", "item")
        similarity = (
            item.get("metadata", {}).get("similarity_score")
            or item.get("score", 0.0)
        )
        header = f"--- Item {i + 1} | {context_type} | similarity: {similarity:.3f} ---"
        display = {"memory": item.get("memory", "")}
        for field in ("event_date", "occurred_at", "temporal_category"):
            val = item.get(field)
            if val:
                display[field] = val
        if item.get("emotion_type"):
            display["emotion_type"] = item["emotion_type"]
        if item.get("category"):
            display["category"] = item["category"]
        if item.get("participants"):
            display["participants"] = item["participants"]
        if item.get("source_evidence"):
            display["source_evidence"] = item["source_evidence"]
        parts.append(f"{header}\n{json.dumps(display, ensure_ascii=False, default=str)}")
    return "\n\n".join(parts)


def _is_reasoning_model(model):
    m = model.lower()
    if re.match(r'^o[0-9]', m):
        return True
    if 'gpt-5' in m:
        return True
    return False


async def call_llm(model, system_prompt, user_message):
    if model.startswith("gemini"):
        import httpx
        api_key = os.environ.get("GEMINI_API_KEY", "")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        payload = {
            "contents": [
                {"role": "user", "parts": [{"text": f"{system_prompt}\n\n{user_message}"}]}
            ],
            "generationConfig": {"temperature": 0.0, "maxOutputTokens": 4096},
        }
        async with httpx.AsyncClient(timeout=90) as client:
            r = await client.post(url, json=payload)
            r.raise_for_status()
            return r.json()["candidates"][0]["content"]["parts"][0]["text"]
    else:
        import openai
        client = openai.AsyncOpenAI()
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
        kwargs = dict(model=model, messages=messages, max_completion_tokens=4096)
        if not _is_reasoning_model(model):
            kwargs["temperature"] = 0.0
        resp = await client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""


async def run_test(prompt_path, model, question_filter=None):
    # Load data
    with open(CHECKPOINT_PATH) as f:
        ckpt = json.load(f)
    fc = ckpt["eval"]["fetched_contexts"]

    with open(DATASET_PATH) as f:
        dataset = json.load(f)

    with open(prompt_path) as f:
        system_prompt = f.read().strip()

    # Default: preference questions. With --question filter: any matching question.
    if question_filter:
        pref_qs = [q for q in dataset if question_filter in q["question_id"] and q["question_id"] in fc]
    else:
        pref_qs = [
            q for q in dataset
            if q.get("question_type") == "single-session-preference" and q["question_id"] in fc
        ]

    if not pref_qs:
        print("No matching questions found.")
        return

    print(f"Model: {model}")
    print(f"Prompt: {prompt_path}")
    print(f"Questions: {len(pref_qs)}")
    print("=" * 80)

    scores_qualitative = []

    for q in pref_qs:
        qid = q["question_id"]
        item_objects = fc[qid].get("item_objects", [])
        ranked_context = build_ranked_context(item_objects)

        q_date = q.get("question_date", "")
        q_date_line = f"Question Date: {q_date}" if q_date else "Question Date: Not specified"
        user_msg = "\n".join([
            f"Question: {q['question']}",
            q_date_line,
            "\nRetrieved Context (raw JSON from memory provider):\n",
            ranked_context,
        ])

        print(f"\n{'='*80}")
        print(f"Q: {q['question']}")
        print(f"Gold: {q['answer']}")
        print(f"Items: {len(item_objects)}")
        print("-" * 40)

        try:
            prediction = await call_llm(model, system_prompt, user_msg)
            print(f"Prediction:\n{prediction}")
        except Exception as e:
            print(f"ERROR: {e}")
            prediction = f"[Error: {e}]"

        print("-" * 40)

        # Quick qualitative check: do key gold words appear?
        gold_lower = str(q["answer"]).lower()
        pred_lower = prediction.lower()
        gold_keywords = [w for w in gold_lower.split() if len(w) > 5]
        if gold_keywords:
            hits = sum(1 for w in gold_keywords if w in pred_lower)
            pct = hits / len(gold_keywords)
            print(f"Keyword overlap: {pct:.0%} ({hits}/{len(gold_keywords)} gold keywords found)")
        print()

    print("=" * 80)
    print("Done.")


def main():
    parser = argparse.ArgumentParser(description="Test preference prompt locally")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT_PATH, help="Path to system prompt .md file")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="LLM model to use")
    parser.add_argument("--question", default=None, help="Filter to one question (partial ID match)")
    args = parser.parse_args()

    asyncio.run(run_test(args.prompt, args.model, args.question))


if __name__ == "__main__":
    main()
