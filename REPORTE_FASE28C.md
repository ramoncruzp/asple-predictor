# Fase 28c — clasificador de órdenes huérfanas

Base: `f6e8696`. Cambios limitados al script de inventario, sus pruebas y este reporte.

## A. Formatos propios `gA` y `gL`

**HECHO** — [scripts/orphan_orders_report.py](scripts/orphan_orders_report.py):20-22,47-76. Reconoce el CID clásico `g<grid>L<nivel><B|S><ciclo>` y los formatos estrictos `gA`/`gL` seguidos por 30 dígitos hexadecimales minúsculos. `fullmatch` impide que `gL...` se confunda con el patrón clásico, que exige dígitos después de `g`.

Pruebas: [tests/test_orphan_orders_report.py](tests/test_orphan_orders_report.py):150-196 cubre `gA`, `gL`, CID ambiguo, `g6L0B0` sin grid y `web_123`.

## B. Dueño por registros locales

**HECHO** — [scripts/orphan_orders_report.py](scripts/orphan_orders_report.py):79-123 cruza IDs de órdenes y CIDs de niveles; las CIDs de préstamo se cargan de `grid_loans.details`. La lectura usa `PRAGMA table_info` y mapea columnas por nombre en :152-194; JSON vacío o malformado se ignora sin fallar. Una coincidencia en grid activo del mismo símbolo es `OK`; una coincidencia registrada que no cumple eso es `HUÉRFANA`; `gA`/`gL` sin registro es `PROPIA_SIN_REGISTRO`; un ID ajeno es `DESCONOCIDA`.

Totales por las cuatro clases y aviso de revisión manual: :248-272. Ninguna clase se cancela automáticamente; `DESCONOCIDA` y `PROPIA_SIN_REGISTRO` requieren revisión antes de cancelar.

Pruebas: [tests/test_orphan_orders_report.py](tests/test_orphan_orders_report.py):198-248 verifica JSON roto/vacío, columnas en orden distinto y la reproducción sintética de `256913 gL60baa…` junto con `279464–279467 gA…`, todas `OK` para el grid 2 activo.

## C. Mutaciones

**HECHO** — [tests/test_fase28b_mutations.py](tests/test_fase28b_mutations.py):178-205 agrega las dos mutaciones requeridas con `str.replace`, `assert count == 1` y comparación SHA-256 antes, mutado y restaurado. La mutación de cruce por ID demuestra `PROPIA_SIN_REGISTRO` en vez de `OK`; quitar el reconocimiento `gL` produce `DESCONOCIDA` en vez de `OK`.

- Cruce por `order_id`: original/restaurado `b258ac775e059aa1f8f5c35d07417e20061f9f824c90926ef471721b0a90725f`; mutado `d91e2be9f95b3138b60de32baaf9d0c35ee9daed142ebf2acf3e4bb31ab7ecaa`.
- Reconocimiento `gL`: original/restaurado `b258ac775e059aa1f8f5c35d07417e20061f9f824c90926ef471721b0a90725f`; mutado `727064b334ad3a1ef9951a2f64a2519fc8a302560de09fab4703a2dae63cdb8a.

## Verificación

- Pruebas dirigidas: **34 passed**.
- Suite completa offline, ejecutada una vez con `--basetemp=.pytest-28c-final`: **1315 passed, 1 skipped, 18 deselected, 0 failed** (exit code 0; 355.90 s).
- El script sigue siendo solo lectura: SQLite `mode=ro`; la prueba AST de ausencia de cancelación/creación de órdenes sigue activa.
- **NO VERIFICADO:** ejecución real contra Testnet; no se conectó a la red.

## Archivos de la fase

`scripts/orphan_orders_report.py`, `tests/test_orphan_orders_report.py`, `tests/test_fase28b_mutations.py`, `REPORTE_FASE28C.md`.

Commit de implementación de Fase 28c: `c679022`.
