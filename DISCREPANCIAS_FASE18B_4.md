# Discrepancias — Fase 18B-4

- Las comprobaciones Playwright ya existentes asumían campos Advisor sin redondeo visual y formato `25.000%` / `$149.13`. M1/M6 requieren mostrar `80.1235`, `120.988`, `25,000 %` y `$149,13`; ajusté esas expectativas en `tests/ui/test_grids_browser.py:94-98, 231-234, 258-263`. Los rangos originales siguen en `data-exact` (`80.123456`, `120.987654`).
- `test_node_out_of_range_preview_explains_existing_backend_rejection` esperaba una traducción española que el API no ofrece; la respuesta real es `current price must be strictly inside range`. Actualicé la expectativa de prueba al texto recibido; no cambió el producto ni el backend (`tests/test_frontend_scanner.py:244-247`).
- Medición M8: `FakeExchange` y velas artificiales no modelan red, latencia Testnet ni la espera real de Binance. El tiempo de 7 s observado fuera del fixture queda **NO VERIFICADO**. No optimicé la ruta.
- La disponibilidad del mínimo numérico se verificó en la respuesta actual (`minimum_cell_usdt`); por eso no se modificaron `grid/structure.py` ni `api/routes/grid_structure.py`.
- La suite completa y los tests live no se ejecutaron por instrucción.
