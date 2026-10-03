# Discrepancias — Fase 17C

1. **Margen bruto de filas no elegibles del Scanner.** La ruta existente de scan omite `components.cost_headroom.measured.fee_pct` cuando el resultado no es elegible. El alcance prohíbe cambiar `api/routes/grids.py` y limita `grid/scanner.py` al spread y `edge_warning`; por eso la UI muestra “no disponible” para margen bruto en esas filas. El margen después del polvo permanece cuando `suggested_structure` lo informa. No se adivina la comisión ni se cambia la ruta existente.
2. **Validación visual.** No se hizo QA visual del navegador: Playwright no está instalado en el entorno. Los scripts pasan `node --check` y hay pruebas estáticas del flujo y del escape de contenido.
3. **Testnet.** No se hicieron llamadas ni aperturas reales; el step 0.1 de la prueba de humo es un supuesto, no un filtro confirmado de Testnet.

No se detectó falta de `dust_qty`, `dust_value_usdt`, `cycles_completed` ni `n_levels` en el endpoint de detalle usado por la pantalla.
