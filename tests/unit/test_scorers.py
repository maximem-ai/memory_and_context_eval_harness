from scorers.exact_f1 import score_f1, f1_score
from scorers.temporal_checker import score_temporal

def test_f1_score():
    assert f1_score("apple", "apple") == 1.0
    assert f1_score("apple", "orange") == 0.0
    assert f1_score("red apple", "apple red") == 1.0 # bag of words
    
def test_score_f1_batch():
    preds = [
        {"prediction": "foo", "gold_answer": "foo"},
        {"prediction": "bar", "gold_answer": "baz"}
    ]
    # 1.0 + 0.0 / 2 = 0.5
    assert score_f1(preds) == 0.5

def test_temporal_checker():
    preds = [
        {"prediction": "it is in the kitchen", "gold_answer": "kitchen"},
        {"prediction": "it is in the bedroom", "gold_answer": "garden"}
    ]
    # 1 correct, 1 wrong -> 0.5
    assert score_temporal(preds) == 0.5
