# Fase 20F-1 — prueba determinista del caché de manifest

## Causa y corrección

`tests/test_vol_provider_per_symbol.py:54-74` prueba invalidación del caché. Los JSON `{"version": 1}` y `{"version": 2}` miden ambos 14 bytes. La firma de producción usa `(st_mtime_ns, st_size)` en `grid/volatility_provider.py:22-40`; por eso, si el `mtime_ns` no cambia, el resultado viejo se sirve correctamente según esa firma. La reproducción temporal fijó el mismo `mtime_ns` en ambos archivos: salida `size_before=14, size_after=14, before=1, after=1`.

La prueba de invalidación ahora avanza explícitamente el `mtime_ns` dos segundos tras reescribir el archivo; la nueva prueba `test_symbol_manifest_cache_signature_limit_same_size_same_mtime` fija la firma y documenta el límite aceptado. No se cambió producción. `config/models_config.py:31-65` usa la misma clase de firma para `load_vol_consensus`; ese límite sigue vigente y no se modificó.

## Verificación

- Rojo determinista: la reproducción confirmó `version: 1` tras la reescritura de igual tamaño y mtime.
- Verde enfocado: `2 passed`; archivo completo: `9 passed`.
- Repetición de la prueba de invalidación: `30/30` pasaron (`1 passed` en cada ejecución).
- Mutación de producción: ignorar la firma hizo fallar la prueba con `assert 1 == 2`. SHA-256 de `grid/volatility_provider.py`: antes `3a956ea856425de307fc12b49421e0039c99b7264da24be9f0b43e076ec821a6`; mutado `c9fe40e0d83bce8101e46e595e0ec9fe6eee453a05a3036c5266eeafae89857e`; restaurado `3a956ea856425de307fc12b49421e0039c99b7264da24be9f0b43e076ec821a6` (idéntico al original).
- Suite no-UI: `968 passed, 1 skipped, 18 deselected, 38 warnings in 83.24s`.
- Suite UI: `106 passed, 2 warnings in 124.06s`.

## Alcance y estado

Preflight: `HEAD 81ef32e`, los 33 archivos previstos de 20F sin commit e índice vacío. El único archivo de prueba cambiado por 20F-1 es `tests/test_vol_provider_per_symbol.py`; se añade este reporte. No se hizo stage, commit ni push. No se abrió `.env` ni se tocó `models/saved/`; no se llamó a Binance/Testnet. Playwright registró y bloqueó accesos a `cdn.jsdelivr.net` y `fonts.googleapis.com`. Los directorios temporales de pytest bajo `%TEMP%` se eliminaron.
