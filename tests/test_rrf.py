from papersim.search import rrf


def test_document_present_in_several_rankings_wins():
    fused = rrf({"semantic": ["a", "b", "c"], "lexical": ["c", "d", "a"]})
    ids = [pid for pid, _ in fused]
    assert ids[0] in {"a", "c"}  # présents dans les deux classements
    assert set(ids[:2]) == {"a", "c"}
    assert set(ids) == {"a", "b", "c", "d"}


def test_rrf_formula():
    fused = dict(rrf({"x": ["a"], "y": ["b", "a"]}, k=60))
    assert fused["a"] == 1 / 61 + 1 / 62
    assert fused["b"] == 1 / 61


def test_weights():
    fused = rrf({"x": ["a"], "y": ["b"]}, weights={"y": 2.0})
    assert fused[0][0] == "b"


def test_empty():
    assert rrf({}) == []
    assert rrf({"x": []}) == []
