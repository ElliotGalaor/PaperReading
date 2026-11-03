from papersim.search import ANY_YEAR, YEAR_SQL, Years


def test_years_active():
    assert not ANY_YEAR.active
    assert Years(2015, None).active
    assert Years(None, 2020).active


def test_years_params_match_sql_placeholders():
    params = Years(2015, 2020).params()
    assert params == {"ymin": 2015, "ymax": 2020}
    for name in params:
        assert f"%({name})s" in YEAR_SQL
