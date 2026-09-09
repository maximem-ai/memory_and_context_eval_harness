# Deviations from published benchmark protocols

This file records every point where this harness departs from the protocols published with
LongMemEval and LoCoMo. It exists so that a number produced here can be compared against a number
produced elsewhere without having to read the source to find the differences.

## LongMemEval

**Set.** The full 500-question set, `LongMemEval_S`, from the official release. No subset, no
resampling, no relabeling. The `--variant s` flag selects it.

**Judge.** Binary LLM-as-judge, CORRECT or WRONG, with `gpt-5-mini` as both the answer model and the
judge model. The original paper uses a different judge configuration, so scores here are not
directly comparable to paper-reported numbers unless the judge is matched.

**Repetition.** The published figure is a single run, not a mean across seeds. It is 460 correct out
of 500, reported as 92.0%.

## LoCoMo

**Categories.** Categories 1 through 4. Category 5, the adversarial set, is excluded, following the
convention used by the original LoCoMo paper and by mem0 and Zep in their published results.
Excluding it raises the reported figure relative to a full-set score, so a LoCoMo number from this
harness should only be compared against other Category 1 to 4 numbers.

**Judge.** Same binary judge and same model as LongMemEval.

**Repetition.** The published figure is a single run, not a mean across seeds.

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
