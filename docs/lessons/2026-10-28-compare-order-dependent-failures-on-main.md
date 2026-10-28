# Compare order-dependent failures on main

An expanded test slice can reveal old failures masked by isolation. Do not assume a new port caused them or call them baseline after an isolated comparison. Run the same preceding suite order on current main and the port, and enable all source-dependent tests. Record failures and counts openly. Here both expense-guard failures reproduced after the same main suite and passed alone on both trees. Root cause still needs its own failing-first investigation; assertions remain unchanged.
