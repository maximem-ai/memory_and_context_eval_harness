# Eval Harness — Framework Architecture

## System Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         EVAL HARNESS                                    │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌───────────────┐    ┌───────────────┐    ┌───────────────┐          │
│  │  BENCHMARKS   │    │   PROVIDERS   │    │    JUDGES     │          │
│  │  (Pluggable)  │    │  (Pluggable)  │    │  (Pluggable)  │          │
│  ├───────────────┤    ├───────────────┤    ├───────────────┤          │
│  │ • LongMemEval │    │ • Synap       │    │ • GPT-4o      │          │
│  │ • Custom      │    │ • Mem0        │    │ • GPT-5-mini  │          │
│  │               │    │ • Zep         │    │ • Gemini      │          │
│  │               │    │ • Supermemory │    │ • Claude      │          │
│  └───────┬───────┘    └───────┬───────┘    └───────┬───────┘          │
│          └────────────────────┼────────────────────┘                   │
│                               ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │                    UNIFIED INTERFACE LAYER                       │  │
│  ├─────────────────────────────────────────────────────────────────┤  │
│  │  Benchmark ABC              Provider ABC                        │  │
│  │  ├─ load()                  ├─ initialize()                     │  │
│  │  ├─ get_questions()         ├─ ingest()                         │  │
│  │  ├─ get_haystack_sessions() ├─ await_indexing()                 │  │
│  │  ├─ get_ground_truth()      ├─ search()                         │  │
│  │  └─ get_question_types()    └─ clear()                          │  │
│  └─────────────────────────────────────────────────────────────────┘  │
│                               │                                        │
│                               ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │                       ORCHESTRATOR                               │  │
│  │  global_ingest()  run()  compare()  run_phase()                 │  │
│  └─────────────────────────────────────────────────────────────────┘  │
│                               │                                        │
└───────────────────────────────┼────────────────────────────────────────┘
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                       EXECUTION PIPELINE                                │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌────────┐  ┌─────────┐  ┌────────┐  ┌──────────┐  ┌──────────┐    │
│  │ INGEST │─▶│ INDEXING │─▶│ SEARCH │─▶│  ANSWER  │─▶│ EVALUATE │    │
│  └───┬────┘  └────┬────┘  └───┬────┘  └────┬─────┘  └────┬─────┘    │
│      ▼            ▼           ▼             ▼             ▼           │
│  checkpoint   checkpoint   results/     hypothesis     score +       │
│  per-session  completion   {qid}.json   per question   retrieval     │
│                                                        metrics       │
│                                                           │           │
│                                                           ▼           │
│                                                     ┌──────────┐     │
│                                                     │  REPORT  │     │
│                                                     └──────────┘     │
│                                                     accuracy,        │
│                                                     latency p50/p95, │
│                                                     Hit@K, MRR, NDCG │
└─────────────────────────────────────────────────────────────────────────┘
```

## Evaluation Modes

```
┌─────────────────────────────────────────────────────────────┐
│                    GLOBAL MODE                               │
│                                                              │
│  All sessions → Single container → All questions search it  │
│                                                              │
│  Container: "longmemeval-synap"                              │
│  ┌──────────────────────────────────────────┐               │
│  │  Session 1  Session 2  ...  Session 940  │               │
│  └──────────────────────────────────────────┘               │
│       ↑ Q1 searches    ↑ Q2 searches    ↑ Q500 searches    │
│                                                              │
│  Pro: Fast (one ingestion), cross-session reasoning          │
│  Con: Noise from irrelevant sessions can confuse answers     │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                   ISOLATED MODE                              │
│                                                              │
│  Each question gets its own container with only relevant     │
│  sessions. No cross-contamination.                           │
│                                                              │
│  Container: "longmemeval-synap_{question_id}"                │
│  ┌────────────┐  ┌────────────┐       ┌────────────┐       │
│  │ Q1: Sess   │  │ Q2: Sess   │  ...  │ Q500: Sess │       │
│  │ 3, 7, 12   │  │ 1, 5       │       │ 8, 44, 201 │       │
│  └────────────┘  └────────────┘       └────────────┘       │
│                                                              │
│  Pro: Clean evaluation, each question sees only its evidence │
│  Con: Slower (N separate ingestions), no cross-session       │
└─────────────────────────────────────────────────────────────┘
```

## Phase Details

### Phase 1: Ingest

```
for each session in benchmark:
  ├─ Format session messages into document
  ├─ Call provider.ingest(sessions, container_tag)
  ├─ Track ingestion IDs in checkpoint
  └─ Mark turns completed after confirmation

Deduplication: sessions already in checkpoint are skipped on resume.
Batching: sessions sent in batches of 50 (configurable).
Concurrency: provider-specific (e.g., Supermemory: 10 concurrent).
```

### Phase 2: Search

```
for each question:
  ├─ Call provider.search(question_text, container_tag, limit=10)
  ├─ Save results to data/runs/{runId}/results/{questionId}.json
  └─ Update checkpoint with result count + duration

Results are saved to disk so answer+evaluate can re-run without re-searching.
```

### Phase 3: Answer

```
for each question:
  ├─ Load search results from disk
  ├─ Build prompt: system_prompt.md + question + ranked context
  ├─ Call LLM (GPT-4o, GPT-5-mini, Gemini, etc.)
  └─ Store hypothesis in checkpoint

System prompt: prompts/system_prompt.md (handles memory vs evidence conflicts,
  knowledge updates, counting, preferences, grounding rules).
```

### Phase 4: Evaluate

```
for each question (in parallel):
  ├─ judge_single(question, hypothesis, ground_truth)
  │   └─ Selects prompt by question type (temporal, preference, etc.)
  │   └─ Returns score (0.0-1.0) + explanation
  ├─ judge_retrieval_quality(question, ground_truth, search_results)
  │   └─ LLM evaluates relevance of each retrieved item
  │   └─ Computes Hit@K, Precision@K, Recall@K, F1@K, MRR, NDCG
  └─ Store all metrics in checkpoint
```

### Phase 5: Report

```
Aggregates across all questions:
  ├─ Overall accuracy (correct / total × 100)
  ├─ Accuracy by question type
  ├─ Latency stats: min, max, mean, median, p95, p99
  │   └─ Per phase: search, answer, evaluate, total
  ├─ Retrieval aggregates: averaged Hit@K, MRR, NDCG, etc.
  └─ Saved to data/runs/{runId}/report.json
```

## Checkpointing

```
data/
├── runs/{runId}/
│   ├── checkpoint.json        # Per-question, per-phase status
│   ├── results/               # Search results saved to disk
│   │   ├── {questionId1}.json
│   │   └── {questionId2}.json
│   └── report.json            # Final aggregated report
│
├── ingest-checkpoints/
│   └── {dataset}.json         # Global ingest state per provider
│
├── comparisons/
│   └── {compareId}/
│       └── comparison.json    # Multi-provider comparison metadata
│
└── leaderboard.json           # Submitted benchmark results
```

**Resume flow:**
1. Load checkpoint → identify completed phases per question
2. Skip completed questions for each phase
3. Resume from first incomplete question
4. Atomic writes (tmp + rename) prevent corruption on crash

## Judge Prompts

Five specialized prompts selected by question type:

| Type | Prompt Strategy | Key Rule |
|------|----------------|----------|
| **Default** | Factual comparison | Score if key facts from gold are present in prediction |
| **Temporal** | Lenient on dates | Allow off-by-one day, equivalent date expressions |
| **Knowledge Update** | Must track updates | Old value only → max 0.5; must have updated value |
| **Preference** | Rubric-based | Does hypothesis correctly apply user preferences? |
| **Adversarial** | Reward refusal | Correct behavior = decline to answer; confident answer = 0.0 |

## Retrieval Metrics

| Metric | What It Measures | Range |
|--------|-----------------|-------|
| **Hit@K** | At least one relevant result in top-K | 0 or 1 |
| **Precision@K** | Fraction of top-K that are relevant | 0.0–1.0 |
| **Recall@K** | Fraction of all relevant items found | 0.0–1.0 |
| **F1@K** | Harmonic mean of Precision and Recall | 0.0–1.0 |
| **MRR** | 1/rank of first relevant result | 0.0–1.0 |
| **NDCG** | Ranking quality (rewards relevant items higher) | 0.0–1.0 |

## Web Dashboard

The Next.js frontend provides:

| Page | Purpose |
|------|---------|
| `/runs` | List all evaluation runs with status, accuracy, filters |
| `/runs/new` | Create new run (provider, benchmark, model, isolation mode) |
| `/runs/{id}` | Run detail: phase progress, accuracy by type, latency, retrieval |
| `/runs/{id}/questions/{qid}` | Question drill-down: ground truth vs hypothesis, context |
| `/compare` | List multi-provider comparisons |
| `/compare/new` | Create comparison (select 2+ providers) |
| `/compare/{id}` | Side-by-side accuracy charts, latency tables |
| `/leaderboard` | Ranked results across providers |
| `/ingest` | Manage data ingestion with per-provider status |

## API Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/api/run` | Start evaluation run |
| POST | `/api/ingest` | Start data ingestion |
| POST | `/api/compare` | Start multi-provider comparison |
| GET | `/api/runs` | List all runs |
| GET | `/api/runs/{id}` | Run detail with all questions |
| GET | `/api/report/{id}` | Run report (metrics) |
| GET | `/api/providers` | List available providers |
| GET | `/api/benchmarks` | List available benchmarks |
| GET | `/api/leaderboard` | List leaderboard entries |
