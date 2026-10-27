# LongMemEval-S runner

`python -m benchmarks.longmemeval.run` runs the LongMemEval **S** split against
Synap with the settings listed below. It is separate from the dashboard
pipeline (`runner/`): it has its own ingest, retrieval, answer and judge
steps, and one command does all of them.

The goal is that anyone can run **the same settings**. It is not a promise to
reproduce a particular score. Synap's extraction and ranking depend on the
configuration of the instance you run against, which this repo does not set.

## Setup

```bash
pip install -r requirements-longmemeval.txt
cp .env.example .env
```

Set in `.env`:

| Variable | Meaning |
|---|---|
| `SYNAP_API_KEY` | Synap API key |
| `SYNAP_INSTANCE_ID` | Synap instance id |
| `SYNAP_BASE_URL` | Optional. Defaults to `https://synap-cloud-prod.maximem.ai` |
| `OPENAI_API_KEY` | The default answer model and the judge are OpenAI. Set the key for whichever provider you choose instead (`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`). |

Download the data (about 277 MB, not committed):

```bash
python scripts/download_datasets.py --dataset longmemeval --variant s
```

This writes `datasets/longmemeval/longmemeval_s_cleaned.json` from the
Hugging Face dataset `xiaowu0162/longmemeval-cleaned`.

## Run

```bash
# smoke test: ingest, retrieve, answer, judge 3 questions
python -m benchmarks.longmemeval.run --user-prefix try1_ --max-questions 3

# full run
python -m benchmarks.longmemeval.run --user-prefix run1_
```

**Use a new `--user-prefix` for every fresh run.** Synap caches extraction by
content hash and the cache survives deletion of a user's data, so re-ingesting
the same text under an old prefix can return an earlier extraction. The prefix
must be the same for the ingest and the evaluation of one run (it is, unless
you pass `--skip-ingest` with a different one; the runner refuses to evaluate
scopes it has no ingest record for).

Before ingest starts, the runner makes one tiny call to each model, so a wrong
key or model id fails in seconds instead of hours in.

### Cost and time

The S split has 500 questions, 23,867 sessions and 246,750 turns. Each
question has its own Synap user holding roughly 40 to 60 sessions, and Synap
indexes the sessions of one user one after another. The 444 questions outside
the single-session-assistant type were estimated at about 50 hours at ingest
concurrency 12. That figure is an estimate, not a measured complete run. LLM
cost is two calls per question (answer, judge).

### Resuming

Everything is resumable. Ingest progress is written to `checkpoints/`
(gitignored) the moment each batch is submitted and again when it is confirmed
stored. Re-run the same command with the same `--run-id` and `--user-prefix`
to continue: stored sessions are not sent again, and answered questions are not
repeated. A question that ended in an error is **retried** on resume.

## Settings (defaults)

| Setting | Default |
|---|---|
| Synap SDK | `maximem-synap==0.5.8` |
| Retrieval mode | accurate (`top_k` and `max_results` 50) |
| Haystack | one Synap user per question |
| Per-turn inline timestamps | off |
| gRPC listen stream | off |
| Search retries | 4, backoff 3 s (doubling) |
| Indexing poll | 3 s up to 15 s, timeout 10800 s, status concurrency 10 |
| Question date sent to search | no |
| Answer prompt | `prompts/qa_agent_subjects_used.md` (`--answer-prompt default`) |
| Answer model | `openai/gpt-5.4-2026-03-05`, temperature 0, seed 7 where supported |
| Source chunks (`source_evidence`) in prompt | with (`--chunks with`) |
| Memory metadata in prompt | on (`--metadata on`) |
| Current date line | added by the runner (`## Current date`) |
| Judge | `openai/gpt-4o`, upstream prompt, temperature 0, `max_tokens` 10 |
| Concurrency | answer 5, search 3, ingest 12 users, batch size 5 |

The Synap-side values live in `configs/synap_longmemeval_s.yaml`.

## Models and providers

`--answer-model` and `--judge-model` take `provider/model`, resolved by
[LiteLLM](https://docs.litellm.ai/): `openai/...`, `anthropic/...`,
`gemini/...` and the other LiteLLM providers. For an OpenAI-compatible
endpoint use the `openai/` prefix with `--answer-api-base` or
`--judge-api-base` (the key comes from `LLM_API_KEY`).

Failed calls are retried with exponential backoff (4 attempts). An
authentication error or an unknown model id is not retried: it stops the run.

**Changing the judge changes the score's meaning.** The judge prompt is
LongMemEval's own and stays the same, but the published numbers use
`gpt-4o`. With any other judge the runner prints a warning, the report says
the score is not comparable to upstream, and the manifest records it
(`judge_matches_upstream: false`). Reasoning models can spend the judge's
10-token budget on hidden reasoning and return nothing; an empty judge reply
becomes an error row, not a wrong answer.

## How scoring works

- One judge call per question, using the question-type-specific prompt from
  the upstream repo (`benchmarks/longmemeval/upstream/`, pinned in
  `PINNED_COMMIT`, MIT licensed). `tests/unit/test_longmemeval_s.py` checks the
  prompts are byte-identical to upstream's.
- The judge gets the **full** generated answer, including the `Answer:` line
  and the explanation after it.
- Correct means the reply contains `yes`. Only the raw reply (`judge_raw`) is
  stored; the verdict is derived from it.
- The 30 abstention questions (`_abs` in the id) are judged with a different
  prompt. They are run and reported separately and are **not** in the headline
  accuracy, which covers the answerable questions only. `summary.json` also
  gives the combined figure.
- A row with an error, or with an empty judge reply, is never counted as
  scored, correct or wrong. The summary reports `scored`, `errors` and
  `not_attempted`, and the run status is `incomplete` until every selected
  question is scored. The exit code is 0 only for a complete run.
- The published gold answers are used as shipped.

## Output

`results/longmemeval/<run_id>/` (gitignored):

| File | Contents |
|---|---|
| `manifest.json` | settings, versions, dataset hash, prompt hash, models, selection, status |
| `questions.jsonl` | one row per attempt: retrieval, the exact prompts, answer, `judge_raw`, timings, error |
| `summary.json` | accuracy, counts, retrieval and latency stats, token usage |
| `by_category.json` | accuracy per question type |
| `REPORT.md` | the same, readable |

`--dump-ingested` also writes the exact documents sent to Synap; it is off by
default because the S haystacks are large.

## Dataset check

`validate()` runs on the whole file before every run, including a 3-question
one, and the run refuses to start unless it is exactly the S split: 500
questions, 23,867 sessions, 246,750 turns, 30 abstention questions, and
knowledge-update 78, multi-session 133, single-session-assistant 56,
single-session-preference 30, single-session-user 70, temporal-reasoning 133.

## Other options

`--question-types`, `--exclude-question-types`, `--exclude-abstention` and
`--question-ids` narrow the run. A narrowed run scores a different
denominator, and the manifest says so. `--ingest-only` and `--skip-ingest`
split ingest from evaluation. `python -m benchmarks.longmemeval.run --help`
lists everything.
