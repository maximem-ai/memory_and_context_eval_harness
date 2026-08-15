import string
import collections
from typing import List, Dict

def normalize_answer(s):
    """Lower text, remove punctuation, and collapse whitespace.

    NOTE: unlike the SQuAD-style normalisation this metric is modelled on,
    articles ("a", "an", "the") are NOT stripped. A dead `remove_articles`
    helper used to sit here that was never called and returned its input
    unchanged, so article stripping has never been applied. Behaviour is
    left as-is on purpose: changing it would move F1 scores. Reinstating it
    should be a deliberate, separately reviewed change.

    This scorer is not currently on the evaluation path. `runner/phases/
    evaluate.py` uses `scorers.llm_judge`, so no published benchmark number
    depends on the behaviour described above.
    """
    def white_space_fix(text):
        return ' '.join(text.split())
    def remove_punc(text):
        exclude = set(string.punctuation)
        return ''.join(ch for ch in text if ch not in exclude)
    def lower(text):
        return text.lower()
    return white_space_fix(remove_punc(lower(s)))

def f1_score(prediction, ground_truth):
    prediction_tokens = normalize_answer(prediction).split()
    ground_truth_tokens = normalize_answer(ground_truth).split()
    common = collections.Counter(prediction_tokens) & collections.Counter(ground_truth_tokens)
    num_same = sum(common.values())
    if num_same == 0:
        return 0
    precision = 1.0 * num_same / len(prediction_tokens)
    recall = 1.0 * num_same / len(ground_truth_tokens)
    f1 = (2 * precision * recall) / (precision + recall)
    return f1

def score_f1(predictions: List[Dict]) -> float:
    scores = []
    for p in predictions:
        pred = p.get("prediction", "")
        gold = p.get("gold_answer", "")
        scores.append(f1_score(pred, gold))
    return sum(scores) / len(scores) if scores else 0.0
