from delivery_api.postgres_scope import statement_label


def test_statement_label_never_copies_sql_values_or_comments():
 for sql in ["SELECT 'private-project-title' FROM tasks WHERE id=123456",'SELECT $$private-dollar-value$$ AS value', 'SELECT 987654321 /* private-comment */', '-- private-header\nSELECT 1', 'SELECT "private-identifier" FROM tasks']:
  label=statement_label(sql)
  assert 'private' not in label and '123456' not in label and '987654321' not in label
  assert label


def test_escaped_nested_and_unterminated_values_fail_closed():
 for sql in ["SELECT E'private\\'project'",'SELECT /* outer /* private */ private */ 1',"SELECT $tag$private",'SELECT "private',"SELECT 'private",'-- private only']:
  assert 'private' not in statement_label(sql)
