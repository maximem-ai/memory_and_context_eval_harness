# Deviations from published benchmark protocols

This file records every point where this harness departs from the protocols published with
LongMemEval and LoCoMo. It exists so that a number produced here can be compared against a number
produced elsewhere without having to read the source to find the differences.

## LongMemEval

**Set.** The full 500-question set, `LongMemEval_S`, from the official release. No subset, no
resampling, no relabeling. The `--variant s` flag selects it.

**Judge.** LongMemEval's own judge (`evaluate_qa.py`, per-question-type yes/no prompts) with
`gpt-4o`, the model the paper's script uses. The answer model is `gpt-5.4`. Abstention questions
use the upstream abstention prompt.

**Abstention.** The all-500 figure (95.2%, 476 of 500) includes the 30 abstention
questions. The answerable-only figure (95.5%, 449 of 470) excludes them.

**Retrieval metric.** Recall@20 (98.1%, 461 of 470) is a hit when `gpt-4o` judges at least one of
the first 20 retrieved memories relevant to the question and gold answer. The relevance prompt is the
one Supermemory uses for its published 97% Recall@20. It is an LLM-judged hit rate, not a labelled one.

**Repetition.** Single run (2026-10-13), not a mean across seeds.

## LoCoMo

**Categories.** Categories 1 through 4. Category 5, the adversarial set, is excluded, following the
convention used by the original LoCoMo paper and by mem0 and Zep in their published results.
Excluding it raises the reported figure relative to a full-set score, so a LoCoMo number from this
harness should only be compared against other Category 1 to 4 numbers.

**Judge.** Same binary judge and same model as LongMemEval.

**Repetition.** The figure is a single run, not a mean across seeds.

## Cross-vendor comparison

Competitor figures reported in the results repository are each vendor's own published accuracy,
taken from their papers and posts. They were not re-run on this harness. Any comparison table built
from them compares a number measured here against numbers measured elsewhere, under each vendor's
own judge and conditions.

## Data handling

**PII.** Input datasets are the public LoCoMo and LongMemEval releases, which are synthetic. The
runner does not detect or redact PII, so do not point it at production conversation data without
adding that step.

**Missing datasets.** If the dataset files are absent, the loaders raise rather than substituting
sample data. See `scripts/download_datasets.py`. A smoke-test path using embedded sample data is
available behind an explicit flag and cannot be reached during a scored run.
