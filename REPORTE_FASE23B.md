# Fase 23b - prestatario desde 1 ciclo y preparación operativa

## Resultado

| Punto | Estado | Evidencia / resultado |
|---|---|---|
| 1. Umbral de prestatario | PARCIAL | `grid/policy.py:43` fija el mínimo predeterminado en 1. `tests/test_grid_loans_pure.py` añade cobertura del predeterminado y conserva el caso explícito de 3 ciclos. Pruebas offline: 63 passed, 1 deselected. La prueba live no pudo validar Testnet (WinError 10013); la revisión automática rechazó elevar el acceso porque la prueba puede operar órdenes. No se hizo commit al no poder completar la compuerta live. |
| 2a. Refresco de velas | NO HECHO | Se intentó `scripts/download_candles.py --symbol XRPUSDT --interval 1h --days 730 --out data/cache/xrp_1h.csv`; el cliente agotó 3 intentos y devolvió `WinError 10013` al conectar con `api.binance.com:443`. El cliente falló al inicializar antes de escribir; no se actualizó el CSV ni se midieron fechas o huecos. |
| 2b. Re-puntuación en copia | NO HECHO | Se copiaron `asple_predictor.db`, `-wal` y `-shm` a `%TEMP%\asple_copy` y se ejecutó el modo dry-run, sin `--apply`. Resultado: `no such column: p.verification_status`. En la copia, `predictions` no tiene `verification_status`; por eso no se produjeron métricas. No se alteró la base real. La consistencia de la copia respecto a una posible app abierta no se pudo determinar. |
| 2c. Filtros públicos de Testnet | HECHO | GET público de exchangeInfo; sin credenciales ni órdenes. XRPUSDT y ADAUSDT: notional mínimo 5 USDT, step 0.1, tick 0.0001. WIFUSDT: notional mínimo 1 USDT, step 0.01, tick 0.0001. Para umbral de celda = 1.3 x notional y capital 100/150/200 USDT, n máximo: XRP/ADA 15/23/30; WIF 76/115/153. |
| 2d. `open-pair` dry-run | NO HECHO | Se revisó `--help`, pero no se ejecutó. El contexto de la CLI instancia settings y clientes de Testnet y consulta estado/mercado; no se inició servidor ni se usó `--execute`. Sin confirmar una ruta de dry-run aislada de la BD real y de la carga de credenciales, no se afirma un resultado operativo. |

## Cambio y pruebas de la Parte 1

- `grid/policy.py:43`: `loan_borrower_min_cycles` cambia de 3 a 1. `tests/test_grid_policy.py` actualiza la expectativa del contrato.
- `tests/test_grid_loans_pure.py`: un prestatario con un ciclo y venta reciente califica con el default; con el valor explícito 3 no califica.
- Rojo inicial: los dos casos mencionados fallaron con el default anterior (3).
- Mutación del default de vuelta a 3: la prueba de comportamiento falló, como se esperaba. SHA-256 original/restaurado `a05d5dd15c9b92e382671303f7658700b5986f18fff6a1777064c8f3a2a256c1`; mutado `71acaf48a3bd2f4fb019ef50dead603f651cd05f4aae95815236734faca5792b`.
- Salida offline dirigida: `63 passed, 1 deselected`. La corrida que incluyó el test live terminó `1 failed, 63 passed`; el fallo fue inicialización/conexión a Testnet con `WinError 10013`, no una aserción de la lógica.

## Efecto sobre grids existentes

`grid/engine.py:280` valida los parámetros Smart al crear y `grid/engine.py:364` persiste `effective_params`; por tanto, los grids creados con el código anterior pueden tener guardado explícitamente `loan_borrower_min_cycles: 3` y no cambian retroactivamente. `database/db_manager.py:1371-1382` decodifica el JSON de parámetros guardado sin rellenar defaults. Si un registro antiguo carece de esa clave y luego se normaliza mediante `validate_params`, `grid/policy.py:117-130` incorpora el default actual (1).

## Límites y verificación pendiente

- La prueba live de préstamos en Testnet queda NO VERIFICADA: el sandbox bloqueó la conexión y la revisión automática rechazó elevarla por el posible efecto de órdenes. No se intentó eludir esa restricción.
- 2a necesita repetirse cuando la conexión pública esté disponible; ejecutar el mismo comando de descarga indicado arriba.
- 2b necesita una copia de base con el esquema esperado (`verification_status` y demás columnas del script) o una migración normal de la copia antes del dry-run; no se hizo ninguna migración.
- 2d necesita un dry-run que use una BD temporal/copia y configuración explícita sin leer credenciales de `.env`; no se ejecutó.
- No se hizo stage, commit ni push. El commit quedó pendiente porque la prueba live no pudo satisfacer la condición de cierre.
