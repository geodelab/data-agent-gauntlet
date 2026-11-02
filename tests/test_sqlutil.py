from adab.sqlutil import compare_results, is_read_only_query, normalize_value, split_statements


def test_split_ignores_semicolons_in_strings_and_comments():
    sql = "SELECT 'a;b'; -- c;d\nSELECT 2; /* e;f */ SELECT 3"
    assert split_statements(sql) == ["SELECT 'a;b'", "-- c;d\nSELECT 2", "/* e;f */ SELECT 3"]


def test_split_drops_comment_only_tail():
    assert split_statements("SELECT 1;\n-- done\n") == ["SELECT 1"]


def test_read_only_query():
    assert is_read_only_query("SELECT * FROM t")
    assert is_read_only_query("-- note\nWITH x AS (SELECT 1) SELECT * FROM x;")
    assert is_read_only_query("SELECT created_at FROM t")  # 'created' is not CREATE
    assert not is_read_only_query("DROP TABLE t")
    assert not is_read_only_query("SELECT 1; DROP TABLE t")
    assert not is_read_only_query("WITH d AS (DELETE FROM t) SELECT 1")


def test_normalize_value():
    assert normalize_value("12.50") == 12.5
    assert normalize_value(3) == 3.0
    assert normalize_value("abc   ") == "abc"
    assert normalize_value("2008-01-07 00:00:00") == "2008-01-07"
    assert normalize_value("2008-01-07 10:30:00") == "2008-01-07 10:30:00"
    assert normalize_value(None) is None


def test_compare_unordered_and_tolerance():
    ok, _ = compare_results([("b", "2.001"), ("a", 1)], [("a", 1.0), ("b", 2)])
    assert ok


def test_compare_ordered_detects_order():
    ok, why = compare_results([("b",), ("a",)], [("a",), ("b",)], ordered=True)
    assert not ok and "row 0" in why


def test_compare_shape_mismatch():
    ok, why = compare_results([(1, 2)], [(1, 2), (3, 4)])
    assert not ok and "row count" in why
    ok, why = compare_results([(1,)], [(1, 2)])
    assert not ok and "column count" in why


def test_relative_tolerance_for_approximate_counts():
    ok, _ = compare_results([(1, 103)], [(1, 100)], rel_tol=0.05)
    assert ok
    ok, _ = compare_results([(1, 110)], [(1, 100)], rel_tol=0.05)
    assert not ok
