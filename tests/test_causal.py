import pandas as pd

from aave_bns.causal import two_by_two_did


def test_two_by_two_did():
    frame = pd.DataFrame({
        "treated": [0, 0, 1, 1],
        "post": [0, 1, 0, 1],
        "y": [1.0, 2.0, 1.5, 4.5],
    })
    result = two_by_two_did(frame, outcome="y")
    assert result.estimate == 2.0
    assert result.synthetic_demo is True
