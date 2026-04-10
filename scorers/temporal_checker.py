from typing import List, Dict

def score_temporal(predictions: List[Dict]) -> float:
    """
    Check if the prediction matches the gold answer for time-sensitive questions.
    For this basic impl, we just compare string equality or inclusion.
    LongMemEval might have complex logic, but we treat it as checking if the *current state* answer is derived.
    """
    correct = 0
    for p in predictions:
        pred = p.get("prediction", "").lower()
        gold = p.get("gold_answer", "").lower()
        if gold in pred:
            correct += 1
    return correct / len(predictions) if predictions else 0.0
