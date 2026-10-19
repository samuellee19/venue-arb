from db.migrate import split_statements


def test_split_statements_drops_comments():
    sql = "-- c\nCREATE TABLE a (x Int8);\n\nCREATE TABLE b (y Int8);"
    assert split_statements(sql) == ["CREATE TABLE a (x Int8)", "CREATE TABLE b (y Int8)"]
