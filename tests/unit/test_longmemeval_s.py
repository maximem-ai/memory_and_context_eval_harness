"""Tests for the LongMemEval-S runner.

No network, no Synap, no LLM. The judge-parity test compares the prompts built
by benchmarks/longmemeval/judge.py against the vendored upstream file, loading
only the upstream function (its module imports packages the runner does not
need).
"""

import ast
import asyncio
import json
import os

import pytest

from benchmarks import models, runio
from benchmarks.common import (
    apply_chunk_mode, future_years_in_metadata, load_provider_config,
    render_context,
)
from benchmarks.longmemeval import dataset as ds
from benchmarks.longmemeval import judge
from benchmarks.longmemeval.run import build_answer_prompt

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UPSTREAM = os.path.join(ROOT, "benchmarks", "longmemeval", "upstream", "src",
                        "evaluation", "evaluate_qa.py")


def _upstream_prompt_fn():
    """Compile only get_anscheck_prompt out of the vendored upstream file."""
    with open(UPSTREAM) as fh:
        tree = ast.parse(fh.read())
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "get_anscheck_prompt")
    ns: dict = {}
    exec(compile(ast.Module([fn], []), UPSTREAM, "exec"), ns)
    return ns["get_anscheck_prompt"]


# ── judge parity ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("task", judge.QUESTION_TYPES)
@pytest.mark.parametrize("abstention", [False, True])
def test_judge_prompt_is_byte_identical_to_upstream(task, abstention):
    up = _upstream_prompt_fn()
    q, gold, resp = "What is the name?", "Alex", "Answer: Alex\nExplanation: x {}"
    assert judge.get_anscheck_prompt(task, q, gold, resp, abstention=abstention) == \
        up(task, q, gold, resp, abstention=abstention)


def test_judge_unknown_type_raises_like_upstream():
    with pytest.raises(NotImplementedError):
        judge.get_anscheck_prompt("not-a-type", "q", "a", "r")
    with pytest.raises(NotImplementedError):
        _upstream_prompt_fn()("not-a-type", "q", "a", "r")


def test_judge_label_rule_and_abstention_selector():
    assert judge.label_from_reply("Yes") and judge.label_from_reply("yes.")
    assert not judge.label_from_reply("No") and not judge.label_from_reply("")
    assert judge.is_abstention("abc_abs") and not judge.is_abstention("abc")


def test_judge_makes_one_call_with_full_answer_and_upstream_settings():
    calls = []

    async def fake(prompt, system, max_tokens):
        calls.append((prompt, system, max_tokens))
        return "yes"

    full = "Answer: 42\nBecause the notes say so."
    v = asyncio.run(judge.score_async(
        "q1", "multi-session", "How many?", "42", full, fake))
    assert v.correct and v.raw == "yes"
    assert len(calls) == 1
    prompt, system, max_tokens = calls[0]
    assert full in prompt          # the whole answer text, not just the Answer: line
    assert system == "" and max_tokens == 10


def test_empty_judge_reply_is_an_error_not_a_wrong_answer():
    async def empty(prompt, system, max_tokens):
        return "   "

    with pytest.raises(judge.EmptyJudgeReply):
        asyncio.run(judge.score_async("q1", "multi-session", "q", "a", "r", empty))


def test_aggregate_excludes_error_and_unjudged_rows():
    rows = [
        {"question_id": "a", "question_type": "multi-session", "judge_raw": "yes"},
        {"question_id": "b", "question_type": "multi-session", "judge_raw": "no"},
        {"question_id": "c", "question_type": "multi-session", "judge_raw": "yes",
         "error": "boom"},                                     # errored: never scored
        {"question_id": "d", "question_type": "multi-session", "judge_raw": ""},
        {"question_id": "e_abs", "question_type": "multi-session",
         "abstention": True, "judge_raw": "yes"},
    ]
    agg = judge.aggregate(rows)
    assert (agg["n"], agg["correct"], agg["accuracy"]) == (2, 1, 0.5)
    assert agg["errored"] == 2
    assert agg["abstention"]["n"] == 1 and agg["abstention"]["correct"] == 1
    assert agg["all_questions"]["n"] == 3


# ── dataset validation ────────────────────────────────────────────────────

def _fixture(n_questions, types=None):
    out = []
    for i in range(n_questions):
        qtype = (types or ["multi-session"])[i % len(types or [1])]
        out.append(ds.Question(
            question_id=f"q{i}" + ("_abs" if i == 0 else ""),
            question_type=qtype, question="q", answer="a", question_date=None,
            sessions=[ds.Session("s0", None, [{"role": "user", "content": "hi"}])]))
    return out


def test_validate_rejects_a_tiny_fixture():
    report = ds.validate(_fixture(3))
    assert not report["ok"]
    joined = " ".join(report["problems"])
    assert "expected 500 questions" in joined
    assert "expected 23867 sessions" in joined
    assert "expected 246750 turns" in joined


def test_validate_accepts_exactly_the_s_split_shape():
    qs, type_cycle = [], []
    for t, n in ds.EXPECTED_TYPES.items():
        type_cycle += [t] * n
    n_abs = 0
    for i, t in enumerate(type_cycle):
        abs_suffix = "_abs" if n_abs < ds.EXPECTED_ABSTENTION and i % 16 == 0 else ""
        n_abs += 1 if abs_suffix else 0
        qs.append(ds.Question(f"q{i}{abs_suffix}", t, "q", "a", None, []))
    assert n_abs == ds.EXPECTED_ABSTENTION
    # 23,867 sessions holding 246,750 turns in total.
    sessions_left, turns_left = ds.EXPECTED_SESSIONS, ds.EXPECTED_TURNS
    for k, q in enumerate(qs):
        count = sessions_left // (len(qs) - k) + (1 if k < sessions_left % (len(qs) - k) else 0)
        for j in range(count):
            q.sessions.append(ds.Session(f"s{j}", None, []))
        sessions_left -= count
    flat = [s for q in qs for s in q.sessions]
    base, extra = divmod(turns_left, len(flat))
    for idx, s in enumerate(flat):
        s.turns = [{"role": "user", "content": "x"}] * (base + (1 if idx < extra else 0))
    report = ds.validate(qs)
    assert report["ok"], report["problems"]


def test_validate_flags_duplicate_user_ids():
    q = _fixture(2)
    q[1].question_id = q[0].question_id
    assert any("not unique" in p for p in ds.validate(q)["problems"])


def test_load_missing_file_names_the_download_command(tmp_path):
    with pytest.raises(FileNotFoundError, match="download_datasets"):
        ds.load(str(tmp_path / "nope.json"))


def test_load_reads_published_gold_unchanged(tmp_path):
    path = tmp_path / "d.json"
    path.write_text(json.dumps([{
        "question_id": "x1", "question_type": "multi-session", "question": "q",
        "answer": 7, "question_date": "2023/01/01 (Sun) 10:00",
        "haystack_session_ids": ["s1"], "haystack_dates": ["2023/01/01 (Sun) 09:00"],
        "haystack_sessions": [[{"role": "user", "content": "hi"}]],
        "answer_session_ids": ["s1"]}]))
    (q,) = ds.load(str(path))
    assert q.answer == "7" and q.sessions[0].date == "2023/01/01 (Sun) 09:00"


def test_user_prefix_rules():
    assert ds.set_user_prefix("run7_") == "run7_"
    try:
        for bad in ("", "Has-Hyphen", "UPPER"):
            with pytest.raises(ValueError):
                ds.set_user_prefix(bad)
    finally:
        ds.set_user_prefix(ds.DEFAULT_USER_PREFIX)


def test_select_rejects_unknown_ids_and_types():
    qs = _fixture(3)
    with pytest.raises(ValueError):
        ds.select(qs, include_ids=["nope"])
    with pytest.raises(ValueError):
        ds.select(qs, include_types=["bogus"])


# ── answer prompt ─────────────────────────────────────────────────────────

ITEMS = [{"memory": "likes tea", "said_at": "2023-01-02",
          "source_evidence": ["User: I like tea"]}]


def test_answer_prompt_has_current_date_question_and_context():
    p = build_answer_prompt("SYSTEM", "What do I like?", ITEMS, "2023/05/01 (Mon) 08:00",
                            include_chunks=True, include_metadata=True)
    assert p.startswith("SYSTEM")
    assert "## Current date\n2023/05/01 (Mon) 08:00" in p
    assert "What do I like?" in p and "likes tea" in p
    assert "said_at: 2023-01-02" in p and "source_evidence" in p


def test_without_chunks_drops_source_evidence_everywhere():
    p = build_answer_prompt("SYSTEM", "q", ITEMS, None,
                            include_chunks=False, include_metadata=False)
    assert "source_evidence" not in p and "said_at" not in p


def test_chunk_mode_markers():
    tpl = "a<!-- chunks:begin -->WITH<!-- chunks:else -->WITHOUT<!-- chunks:end -->b"
    assert apply_chunk_mode(tpl, True) == ("aWITHb", 1)
    assert apply_chunk_mode(tpl, False) == ("aWITHOUTb", 1)
    assert apply_chunk_mode("plain", False) == ("plain", 0)


def test_blacklisted_metadata_is_never_rendered():
    item = {"memory": "m", "extracted_at": "2031-01-01", "detected_at": "2031-01-01",
            "occurred_at": "2031-01-01", "event_date": "2023-01-01"}
    text = render_context([item], include_chunks=False, include_metadata=True)
    assert "2031" not in text and "event_date: 2023-01-01" in text


def test_future_year_guard_ignores_years_the_memory_names():
    named = {"memory": "aims for 50% by 2025", "valid_until": "2025-12-31"}
    leak = {"memory": "likes tea", "valid_until": "2031-01-01"}
    assert future_years_in_metadata([named]) == []
    assert future_years_in_metadata([leak])[0]["year"] == 2031


# ── config, models, results ───────────────────────────────────────────────

def test_config_expands_env_and_defaults_base_url(monkeypatch):
    monkeypatch.setenv("SYNAP_API_KEY", "k")
    monkeypatch.setenv("SYNAP_INSTANCE_ID", "inst")
    monkeypatch.delenv("SYNAP_BASE_URL", raising=False)
    cfg = load_provider_config("configs/synap_longmemeval_s.yaml")
    assert cfg.api_key == "k" and cfg.extras["instance_id"] == "inst"
    assert cfg.base_url == "https://synap-cloud-prod.maximem.ai"
    e = cfg.extras
    assert (e["mode"], e["retrieval_mode"], e["top_k"], e["retrieval_max_results"]) == \
        ("accurate", "accurate", 50, 50)
    assert e["inline_turn_timestamps"] is False and e["enable_grpc_listen"] is False
    assert (e["search_retries"], e["search_backoff_seconds"]) == (4, 3.0)
    assert (e["indexing_poll_interval"], e["indexing_max_interval"],
            e["indexing_timeout_s"], e["indexing_status_concurrency"]) == (3.0, 15.0, 10800, 10)


def test_model_specs_need_a_provider_prefix():
    assert models.split_model("openai/gpt-4o") == ("openai", "gpt-4o")
    assert models.split_model("anthropic/claude-x") == ("anthropic", "claude-x")
    with pytest.raises(ValueError):
        models.split_model("gpt-4o")


def test_missing_key_names_the_variable(monkeypatch):
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert models.missing_key("openai/gpt-4o") == "OPENAI_API_KEY"
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    assert models.missing_key("openai/gpt-4o") is None
    assert models.missing_key("someprovider/model") is None   # left to LiteLLM


def test_resume_retries_errored_rows_and_counts_each_question_once(tmp_path):
    w = runio.QuestionWriter(str(tmp_path))
    base = dict(user_id="u", question_type="multi-session", question="q", gold_answer="a")
    w.write(runio.QuestionRow(question_id="x", error="LLM unavailable", **base))
    w.write(runio.QuestionRow(question_id="y", judge_raw="yes", **base))
    w.write(runio.QuestionRow(question_id="x", judge_raw="no", **base))   # the retry
    w.close()
    rows = runio.read_rows(str(tmp_path))
    assert sorted(r["question_id"] for r in rows) == ["x", "y"]
    assert all(judge.is_scored(r) for r in rows)
    assert not any("judge_correct" in r for r in rows)   # verdict is derived


def test_shipped_default_prompt_renders_in_both_chunk_modes():
    from benchmarks.common import DEFAULT_ANSWER_PROMPT, load_answer_prompt
    assert DEFAULT_ANSWER_PROMPT.endswith("qa_agent_subjects_used.md")
    tpl = load_answer_prompt("default")
    for chunks in (True, False):
        template, blocks = apply_chunk_mode(tpl, chunks)
        assert blocks > 0
        p = build_answer_prompt(template, "What did I buy?", ITEMS, "2023/05/01 (Mon) 08:00",
                                include_chunks=chunks, include_metadata=True)
        assert "## Current date\n2023/05/01 (Mon) 08:00" in p
        assert "What did I buy?" in p and "likes tea" in p
        assert p.rstrip().endswith("Answer:")
        assert ("source_evidence" in p) == chunks
