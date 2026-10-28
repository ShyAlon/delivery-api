# Debug collection must use real readers

Existing database diagnostic readers do not help incident recovery if the Debug
package never calls them. Collect each reviewed operator projection explicitly,
record its availability and original truncation/window fields, and keep customer
policy exclusions distinct from failed or empty reads. Filter logs by selected
task/session fields, not substrings. An unavailable source belongs in the package
manifest, not in an instruction to run Docker or SQL outside the app.
