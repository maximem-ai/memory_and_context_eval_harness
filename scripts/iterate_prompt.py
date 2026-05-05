"""
iterate_prompt.py — fast offline iteration of the Locomo answer system prompt.

Reuses pre-fetched Synap context from a completed run. Runs answer + judge
with a custom prompt and reports scores. **No re-fetching from Synap**, so
iteration takes ~minutes instead of ~hour.

Usage:
    python scripts/iterate_prompt.py \\
        --prompt prompts/locomo_agent_v7.md \\
        --run-id synap-locomo-20260501-142208 \\
        --filter adversarial \\
        --concurrency 10 \\
        --output /tmp/iter_v7.json

Compare to baseline:
    python scripts/iterate_prompt.py ... --baseline synap-locomo-20260501-142208
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict

# Run from repo root regardless of cwd
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

from dotenv import load_dotenv
load_dotenv()

from runner.phases.answer import _call_llm, DEFAULT_ANSWER_TEMPLATE
from scorers.llm_judge import judge_single

DATA_DIR = _ROOT / "data" / "runs"


def _format_context_chronological(items):
    """Format Synap search results as chronological plain text.

    Keeps the fields the LLM benefits from (memory text, event_date,
    valid_until, temporal_category, context_type, source_evidence). Drops
    noise (id, similarity_score, source, extracted_at, temporal_confidence,
    internal metadata). Sorted oldest-first by event_date so temporal
    reasoning is easier.
    """
    if not items:
        return "(No relevant memories retrieved.)"

    def _date_key(it):
        ed = it.get("event_date") or it.get("occurred_at") or ""
        return (0, str(ed)) if ed else (1, "")

    sorted_items = sorted(items, key=_date_key)

    def _format_evidence(ev):
        if ev is None:
            return None
        if isinstance(ev, list):
            # list of strings, list of dicts {speaker, text, dia_id}, etc.
            parts = []
            for e in ev[:3]:
                if isinstance(e, dict):
                    txt = e.get("text") or str(e)
                    spk = e.get("speaker")
                    parts.append(f"{spk}: {txt}" if spk else str(txt))
                else:
                    parts.append(str(e))
            return " | ".join(parts)
        return str(ev)

    lines = ["The following memories are presented in chronological order (oldest event first).", ""]
    for it in sorted_items:
        ctype = it.get("context_type", "memory")
        memory = (it.get("memory") or "").strip()
        ed = it.get("event_date") or it.get("occurred_at")
        valid_until = it.get("valid_until")
        tcat = it.get("temporal_category")

        # Date label
        if tcat == "perpetual":
            date_label = "perpetual"
        elif ed and valid_until and str(ed) != str(valid_until):
            date_label = f"{ed} → {valid_until}"
        elif ed:
            date_label = str(ed)
        else:
            date_label = "undated"

        lines.append(f"[{ctype}, {date_label}] {memory}")

        ev_text = _format_evidence(it.get("source_evidence"))
        if ev_text:
            # Truncate very long source evidence so the prompt doesn't blow up
            if len(ev_text) > 800:
                ev_text = ev_text[:800] + "…"
            lines.append(f"    source_evidence: {ev_text}")

    return "\n".join(lines)


async def _judge_with_template(template_str, question, gold, prediction, model):
    """Run a custom judge prompt template that returns JSON with score+reason."""
    from scorers.llm_judge import _llm_call, _parse_score
    prompt = template_str.format(question=question, gold=gold, prediction=prediction)
    text = await _llm_call(model, prompt, retries=3, max_output_tokens=2048)
    return _parse_score(text)


async def process_one(qid, qcp, sr, system_prompts, model, judge_model,
                      context_format="json", custom_judge_template=None):
    """Run answer + judge for one question. Returns dict with score + diagnostics.

    `system_prompts` is a dict keyed by question_type with a "default" fallback,
    so we can dispatch a different prompt to cat 1-4 vs cat 5.
    """
    if context_format == "chronological":
        context = _format_context_chronological(sr.get("results", []))
    else:
        context = json.dumps(sr.get("results", []), default=str, indent=2)
    user_msg = DEFAULT_ANSWER_TEMPLATE.format(
        question=qcp["question"],
        question_date=qcp.get("question_date") or "unknown",
        context=context,
    )

    qt = (qcp.get("question_type") or "").lower()
    sp = system_prompts.get(qt) or system_prompts.get("default") or ""

    t0 = time.monotonic()
    try:
        hypothesis = await _call_llm(user_msg, model, sp)
    except Exception as e:
        return {"qid": qid, "error": f"answer: {e}", "score": None,
                "question_type": qcp.get("question_type"), "elapsed_ms": 0}

    try:
        if custom_judge_template:
            verdict = await _judge_with_template(
                custom_judge_template, qcp["question"], qcp["ground_truth"], hypothesis, judge_model,
            )
        else:
            verdict = await judge_single(
                question=qcp["question"],
                prediction=hypothesis,
                gold=qcp["ground_truth"],
                model=judge_model,
                question_type=qcp.get("question_type", ""),
            )
    except Exception as e:
        return {"qid": qid, "hypothesis": hypothesis, "error": f"judge: {e}",
                "score": None, "question_type": qcp.get("question_type"),
                "elapsed_ms": round((time.monotonic() - t0) * 1000)}

    return {
        "qid": qid,
        "question": qcp["question"],
        "ground_truth": qcp["ground_truth"],
        "question_type": qcp.get("question_type"),
        "hypothesis": hypothesis,
        "score": verdict.get("score"),
        "reason": verdict.get("reason"),
        "result_count": len(sr.get("results", [])),
        "elapsed_ms": round((time.monotonic() - t0) * 1000),
    }


def _load_baseline_scores(baseline_id: str) -> dict:
    """Pull per-question scores from a previous run for diff display."""
    cp_path = DATA_DIR / baseline_id / "checkpoint.json"
    if not cp_path.exists():
        return {}
    cp = json.loads(cp_path.read_text())
    out = {}
    for qid, qcp in cp.get("questions", {}).items():
        e = (qcp.get("phases", {}) or {}).get("evaluate", {})
        if isinstance(e, dict) and e.get("score") is not None:
            out[qid] = e["score"]
    return out


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prompt", help="Path to default system prompt file (used for all categories unless overridden)")
    ap.add_argument("--prompt-cat14", help="System prompt for cat 1-4 (single-hop, multi-hop, temporal, open-ended)")
    ap.add_argument("--prompt-cat5", help="System prompt for cat 5 (adversarial)")
    ap.add_argument("--judge-prompt", help="Path to a judge-prompt template (uses {question}/{gold}/{prediction}). If set, replaces all category-specific judges with this single template.")
    ap.add_argument("--run-id", required=True, help="Source run for fetched context")
    ap.add_argument("--filter", help="Filter TO a single question_type (e.g. adversarial, multi-hop)")
    ap.add_argument("--exclude", help="Exclude question_types (comma-separated, e.g. 'adversarial' for Mem0-style cat-1-4 scoring)")
    ap.add_argument("--qids", help="Comma-separated specific question_ids to test")
    ap.add_argument("--limit", type=int, help="Max questions to process")
    ap.add_argument("--concurrency", type=int, default=10)
    ap.add_argument("--model", default="gpt-5-mini")
    ap.add_argument("--judge-model", default="gpt-5-mini")
    ap.add_argument("--baseline", help="Run-id to diff against (uses checkpoint scores)")
    ap.add_argument("--context-format", choices=["json", "chronological"], default="json",
                    help="How to format retrieved context for the LLM (default: json — current production)")
    ap.add_argument("--output", help="Save full results JSON")
    args = ap.parse_args()

    # Load prompts (default + per-category)
    system_prompts: Dict[str, str] = {}
    if args.prompt:
        p = Path(args.prompt)
        if not p.exists():
            print(f"ERROR: prompt file not found: {p}")
            sys.exit(1)
        system_prompts["default"] = p.read_text().strip()
        print(f"Prompt (default):  {p} ({len(system_prompts['default'])} chars)")

    cat14_types = ["single-hop", "multi-hop", "temporal", "open-ended"]
    if args.prompt_cat14:
        p = Path(args.prompt_cat14)
        if not p.exists():
            print(f"ERROR: cat14 prompt not found: {p}")
            sys.exit(1)
        cat14_text = p.read_text().strip()
        for t in cat14_types:
            system_prompts[t] = cat14_text
        print(f"Prompt (cat 1-4):  {p} ({len(cat14_text)} chars)")

    if args.prompt_cat5:
        p = Path(args.prompt_cat5)
        if not p.exists():
            print(f"ERROR: cat5 prompt not found: {p}")
            sys.exit(1)
        cat5_text = p.read_text().strip()
        system_prompts["adversarial"] = cat5_text
        print(f"Prompt (cat 5):    {p} ({len(cat5_text)} chars)")

    if not system_prompts:
        print("ERROR: must provide --prompt and/or --prompt-cat14/--prompt-cat5")
        sys.exit(1)

    custom_judge_template = None
    if args.judge_prompt:
        p = Path(args.judge_prompt)
        if not p.exists():
            print(f"ERROR: judge prompt not found: {p}")
            sys.exit(1)
        custom_judge_template = p.read_text().strip()
        print(f"Judge prompt:      {p} ({len(custom_judge_template)} chars)")

    run_dir = DATA_DIR / args.run_id
    cp_path = run_dir / "checkpoint.json"
    if not cp_path.exists():
        print(f"ERROR: run not found: {cp_path}")
        sys.exit(1)
    cp = json.loads(cp_path.read_text())
    questions = cp["questions"]
    print(f"Source:  {args.run_id} ({len(questions)} questions)")

    # Build work list — must have search results on disk
    qid_filter = set(args.qids.split(",")) if args.qids else None
    excluded_types = set(args.exclude.split(",")) if args.exclude else set()
    items = []
    skipped_no_results = 0
    for qid, qcp in questions.items():
        if qid_filter and qid not in qid_filter:
            continue
        if args.filter and qcp.get("question_type") != args.filter:
            continue
        if qcp.get("question_type") in excluded_types:
            continue
        sr_path = run_dir / "results" / f"{qid}.json"
        if not sr_path.exists():
            skipped_no_results += 1
            continue
        items.append((qid, qcp, json.loads(sr_path.read_text())))

    if args.limit:
        items = items[:args.limit]

    if skipped_no_results:
        print(f"Note:    skipped {skipped_no_results} questions (no search-result file)")
    print(f"Filter:  question_type={args.filter or '(all)'}, count={len(items)}")
    print(f"Models:  answer={args.model}, judge={args.judge_model}, concurrency={args.concurrency}")
    print(f"Format:  context={args.context_format}")
    print()

    # Process with bounded concurrency
    sem = asyncio.Semaphore(args.concurrency)
    progress = {"done": 0}
    total = len(items)

    async def bounded(item):
        async with sem:
            r = await process_one(*item, system_prompts, args.model, args.judge_model, args.context_format, custom_judge_template)
            progress["done"] += 1
            if progress["done"] % 10 == 0 or progress["done"] == total:
                print(f"  [{progress['done']:>3}/{total}] processed", flush=True)
            return r

    t_start = time.monotonic()
    results = await asyncio.gather(*(bounded(it) for it in items))
    elapsed = time.monotonic() - t_start

    # Aggregate
    scores = [r["score"] for r in results if r.get("score") is not None]
    by_type = defaultdict(list)
    for r in results:
        if r.get("score") is not None:
            by_type[r.get("question_type", "?")].append(r["score"])

    print()
    print("=" * 60)
    print(f"Wall clock: {elapsed:.1f}s ({total/elapsed:.2f} q/s)")
    print(f"Errors:     {sum(1 for r in results if r.get('error'))}/{len(results)}")
    if scores:
        print(f"Avg score:  {sum(scores)/len(scores):.3f}")
        print(f"Correct:    {sum(1 for s in scores if s >= 0.5)}/{len(scores)} = {100*sum(1 for s in scores if s >= 0.5)/len(scores):.1f}%")
    print()
    print("By question type:")
    for t in sorted(by_type):
        ss = by_type[t]
        print(f"  {t:14s} n={len(ss):3d}  avg={sum(ss)/len(ss):.3f}  correct={sum(1 for s in ss if s >= 0.5)}/{len(ss)}")

    # Baseline diff
    if args.baseline:
        baseline = _load_baseline_scores(args.baseline)
        print()
        print(f"vs baseline {args.baseline}:")
        improved, regressed, unchanged, new_only = [], [], [], []
        for r in results:
            qid = r["qid"]
            if r.get("score") is None: continue
            new_s = r["score"]
            base_s = baseline.get(qid)
            if base_s is None:
                new_only.append(qid); continue
            if new_s > base_s + 0.1: improved.append((qid, base_s, new_s))
            elif new_s < base_s - 0.1: regressed.append((qid, base_s, new_s))
            else: unchanged.append(qid)
        print(f"  Improved:  {len(improved):>3} (e.g. {[f'{q}:{a:.2f}->{b:.2f}' for q,a,b in improved[:3]]})")
        print(f"  Regressed: {len(regressed):>3} (e.g. {[f'{q}:{a:.2f}->{b:.2f}' for q,a,b in regressed[:3]]})")
        print(f"  Unchanged: {len(unchanged):>3}")
        if baseline:
            base_avg = sum(baseline.get(r["qid"], 0) for r in results if r.get("score") is not None) / max(1, len(scores))
            new_avg  = sum(scores) / max(1, len(scores))
            print(f"  Avg shift: {base_avg:.3f} -> {new_avg:.3f}  (Δ {new_avg-base_avg:+.3f})")

    # Save full results
    if args.output:
        Path(args.output).write_text(json.dumps({
            "prompt_path": args.prompt or args.prompt_cat14 or args.prompt_cat5,
            "run_id": args.run_id,
            "filter": args.filter,
            "model": args.model,
            "judge_model": args.judge_model,
            "results": results,
            "agg": {
                "avg": sum(scores)/len(scores) if scores else 0,
                "correct": sum(1 for s in scores if s >= 0.5),
                "total": len(scores),
                "by_type": {t: {"n": len(ss), "avg": sum(ss)/len(ss),
                                "correct": sum(1 for s in ss if s >= 0.5)}
                            for t, ss in by_type.items()},
            },
        }, indent=2, default=str))
        print(f"\nFull results: {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
