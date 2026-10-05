# Discrepancias — Fase 19C-3b

1. `REPORTE_FASE19C3.md` conserva el snapshot previo (2026-10-05 21:23 UTC). No se calculó el después sobre la base local actual; las cifras están marcadas NO VERIFICADO en `REPORTE_FASE19C3B.md`.
2. Las cuatro mutaciones indicadas por el prompt (restaurar fórmula absoluta, invertir dirección, permitir aplicación con k_raw < 1, omitir k2) no se inyectaron y restauraron byte por byte. No se afirma cobertura de mutación.
3. Se añadió migración aditiva de tres columnas a `database/db_manager.py` para permitir guardar `k_raw`, `bias_log` y `vol_scale_suggested` en instalaciones con una tabla 19C-3 preexistente. Es necesaria para exponer los campos requeridos por API/UI.
4. Los nombres de los módulos y pruebas de la fase sí coinciden. La etiqueta anterior exacta se halló en `frontend/app.js` y se actualizó junto a su tabla de dispersión y leyenda.
