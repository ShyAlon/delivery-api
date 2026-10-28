# UI fixtures can use real auth with mocked content

A frontend decision contract may mock typed review payloads without booting an anonymous SQLite runtime. Use the real restricted PG app and signed browser session, keeping payload mocks and behavioral assertions unchanged. Name what is still mocked so a browser pass is not mistaken for backend decision execution. Keep shared legacy runner/auth fixture migrations separate rather than forcing their old assumptions through a blanket cloud flag.
