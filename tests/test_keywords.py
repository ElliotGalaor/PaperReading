from papersim.filesearch import analyze
from papersim.keywords import (
    extract_keywords,
    theme_name,
    themes_from_categories,
    themes_from_results,
)

TITLE = "Finite-Temperature Transconductance of Multiterminal Josephson Junctions with Topological Andreev Spectra"
ABSTRACT = (
    "Multiterminal Josephson junctions provide a controllable platform to study topological Andreev "
    "spectra. We compute the transconductance of Andreev bound states at finite temperature and show "
    "that nonzero Chern numbers survive thermal broadening. The superconducting terminals are coupled "
    "through a normal region, and finite-energy topological crossings produce a quantized response "
    "that we analyze with a scattering approach."
)


def test_keywords_quality():
    kw = extract_keywords(f"{TITLE}. {ABSTRACT}", body=ABSTRACT)
    assert 5 <= len(kw) <= 8
    assert "multiterminal Josephson junctions" in kw
    joined = " ".join(kw)
    assert "Andreev" in joined                   # nom propre conservé
    assert "Finite-Temperature" not in joined    # majuscules de titre retirées
    for k in kw:
        words = k.lower().split()
        assert words[0] not in {"the", "of", "and", "we"} and words[-1] not in {"the", "of", "and"}
        assert not (len(words) > 1 and words[-1].endswith("ed"))
        assert "$" not in k


def test_keywords_no_overlap():
    kw = extract_keywords(f"{TITLE}. {ABSTRACT}", body=ABSTRACT)
    sets = [set(k.lower().split()) for k in kw]
    for i, a in enumerate(sets):
        for b in sets[i + 1:]:
            assert len(a & b) / min(len(a), len(b)) < 0.5


def test_keywords_short_or_empty_text():
    assert extract_keywords("") == []
    assert extract_keywords("Too short") == []


def test_theme_names():
    assert theme_name("cond-mat.supr-con") == "Supraconductivité"
    assert theme_name("math.NT") == "Théorie des nombres"
    assert theme_name("Physics") == "Physique"
    assert theme_name("cs.SE") == "Informatique (cs.SE)"  # catégorie non traduite : archive + code
    assert theme_name("unknown") == "unknown"


def test_themes_from_categories_keeps_primary_first():
    themes = themes_from_categories(["cond-mat.supr-con", "cond-mat.str-el", "cond-mat.supr-con", "quant-ph", "hep-th"])
    assert [t["code"] for t in themes] == ["cond-mat.supr-con", "cond-mat.str-el", "quant-ph"]


def test_themes_from_results_weights_by_rank():
    results = [
        {"paper": {"categories": ["quant-ph"]}},
        {"paper": {"categories": ["cond-mat.supr-con"]}},
        {"paper": {"categories": ["cond-mat.supr-con"]}},
        {"paper": {"categories": None}},
    ]
    # quant-ph : 1 ; supr-con : 1/2 + 1/3 ≈ 0.83
    assert [t["code"] for t in themes_from_results(results)] == ["quant-ph", "cond-mat.supr-con"]


def test_analyze_sources():
    results = [{"paper": {"categories": ["cond-mat.supr-con"]}}]
    own = analyze(TITLE, ABSTRACT, "", ["quant-ph"], results)
    assert own["themes_source"] == "paper" and own["themes"][0]["code"] == "quant-ph"
    inferred = analyze(TITLE, None, f"{TITLE}. {ABSTRACT}", [], results)
    assert inferred["themes_source"] == "similar" and inferred["themes"][0]["code"] == "cond-mat.supr-con"
    assert inferred["keywords"]
