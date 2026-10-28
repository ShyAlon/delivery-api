# A SQL prefix is not a safe statement label

Whitespace normalization and length caps do not remove inline SQL values. Timing
labels included in the operator package must redact literals, comments, quoted
identifiers and numeric values before truncation. Preserve code-owned unquoted
SQL shape so existing timing comparisons remain useful. Handle nested comments
and unterminated quoted values by discarding their content, not copying it.
