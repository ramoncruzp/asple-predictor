# Discrepancias Fase 20C-b

| Punto | Estado y límite |
|---|---|
| D4 | Parcial: los filtros y el límite cliente 30→100 están probados. No se implementó ni verificó una solicitud de página siguiente al API; el comportamiento del endpoint con límite no está confirmado. |
| D6 / R10 | No verificado visualmente: requiere comprobar en navegador con Lightweight Charts real. La prueba actual usa una librería simulada y solo valida `rightOffset`/ancho del contenedor. |
| R6 | `/api/predictions/history` no filtra por intervalo. Sin cambiar API, `battle.js` excluye en cliente las filas cuyo campo `interval` exista y no sea `1h`; las filas sin ese campo se conservan. |
| Fallo grid monitor | En esta corrida final no hubo fallo: `83 passed, 1 skipped`. `tests/test_grid_monitor.py` se mantuvo intacto y se ejecutó antes de los UI tests. |
| Cambios previos fuera del alcance de 20C-b | Se conservaron sin editar los cambios preexistentes de `frontend/app.js`, `frontend/index.html`, `frontend/models.js` y `tests/ui/test_models_page.py`. No se modificaron `scripts/`, `models/`, `api/` ni `database/`. |
| Exclusiones | `models/saved/metrics_xrp_1h.json`, `models/saved/*.joblib`, `models/saved/*.pt`, `logs/training/`, `.pytest_tmp/` y el `.bak` no se editaron ni añadieron al stage. El SHA posterior del JSON de métricas sigue sin haberse capturado desde la fase anterior. |

Las mutaciones (a)–(e) fallaron en las aserciones de sus pruebas y fueron restauradas byte a byte; los cinco SHA antes/mutado/restaurado y salidas literales están en `REPORTE_FASE20C.md`. No hubo stage, commit ni push.
