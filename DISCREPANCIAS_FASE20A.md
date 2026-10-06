# Discrepancias y pendiente — Fase 20A

## Antes y después de V1–V6

| Versión | Antes | Después / límite actual |
|---|---|---|
| V1 — Traslado | Battle de Volatilidad y su carga estaban en Battle. | Tarjeta quitada; enlace a Modelos y Battle de Dirección intacta. Se trasladó y reutilizó la lógica central del gráfico; tabla extendida para soportar dos vistas. |
| V2 — Histórico/en vivo | Sin selector, puestos, Δ ni ordenación. | Selectores y tablas separadas; orden asc/desc, Puesto y Δ solo con n verificada ≥30; un aviso de muestra en vista viva. |
| V3 — Cobertura/gráfico/CSV | Cobertura fija sin alcance; sin serie/ventana/CSV. | Rotula cobertura solo 4h conforme al endpoint actual. Campeón y modelo tienen serie; Consenso aparece como selector pero el API no entrega esa serie y se declara no disponible. 7d/30d/Todo; CSV escapado; Todo topa en 1000 filas. |
| V4 — QLIKE | Sin QLIKE visible. | HAR 0.313457 y HAR_asym 0.313534 a 4h; distintos. Datos del manifiesto local. |
| V5 — Codificación | index.html no explícito. | Test incluye index.html; pasó el patrón solicitado. |
| V6 — Pruebas | Guardas agrupadas. | Render tests separados y Playwright ampliado; seis mutaciones fallaron por aserciones conductuales y restauraron el SHA base. No se ejecutó suite completa. |

## No completado o no verificado

- /api/volatility/history no tiene serie histórica de consenso; al seleccionar Consenso el gráfico muestra explicación y no datos inventados.
- El endpoint de cobertura recibe symbol e interval, no horizon. Se rotula disponible solo a 4h; no se cambió API.
- Todo pide como máximo 1000 registros.
- CDN cdn.jsdelivr.net fue bloqueada por Playwright; visualización real del gráfico no verificada. Selección, filtrado por ventana, consulta de modelo, estados y CSV sí fueron probados.
- **NO VERIFICADO:** datos productivos de otras monedas y comportamiento con varios meses de resultados.
- Observaciones_UI_Modelos.md no estaba disponible en el checkout.
- No se ejecutó suite completa ni se usó Binance/Testnet.

## Pruebas relacionadas con Battle

tests/ui/test_frontend_format.py::test_shadow_cards_validation_labels_and_volatility_detail_are_explicit dependía del helper renderVolBattleTable y #vol-battle-table. Se conservó y ahora carga frontend/models.js y verifica la tabla histórica trasladada. tests/ui/test_grids_browser.py::test_battle_shows_validation_and_prediction_time_volatility_range prueba Battle de Dirección mediante #battle-content y sigue ahí. No se borraron pruebas.

No se hizo stage, commit ni push. No se leyó .env; no se ejecutó Binance/Testnet.
