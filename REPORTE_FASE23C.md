# Fase 23c - cierre M1-M3

## Resultado

| Item | Estado | Evidencia |
|---|---|---|
| M1 | HECHO | `api/routes/grid_control.py:71`, `grid/control_service.py:204-216,245-249`; reutiliza `POST /api/grids/{grid_id}/params`. El global bloquea el encendido con 422 y el motivo acordado; apagar queda permitido para Smart. Simple conserva su rechazo. Se mantienen `dry_run`/`confirm` y el evento `GRID_ACTION_API`. |
| M2 | HECHO | `api/routes/grids.py:1046-1049`; el detalle `/api/grids/{id}/loans` incluye estado por grid y estado global. Prueba de contrato en `tests/test_grid_level_adjust_summary.py:75-86`. |
| M3 | HECHO | `frontend/grids.js:353-376,398`; botón solo para Smart ACTIVE/PAUSED, con estado deshabilitado cuando el global está apagado. Reutiliza preview y confirmación del endpoint de params. Advertencia en ambos pasos del diálogo. Prueba en `tests/ui/test_grids_browser.py:1101-1155`. |

Los archivos `api/routes/grid_control.py` y `grid/control_service.py` quedan **autorizados por enmienda** para M1.

## Verificación

- Rojo inicial: `tests/test_grid_control_api.py tests/test_grid_level_adjust_summary.py -q`: `3 failed, 18 passed`; faltaban el campo de entrada, la protección de M1 y los campos de M2.
- Verde API: `21 passed, 2 warnings`.
- Verde UI dirigida: `1 passed, 45 deselected, 2 warnings`.
- Mutación M1, retirar la guarda global: la prueba falló porque se obtuvo 200 en lugar de 422. SHA-256 antes/restaurado `11476a3bc0f73742e6b805b46d9de1ec8e70ec64deda3688763d9e25e7677c99`; mutado `ff96360f58887db64e848af246c7750c14d123ece4cc55fe803c5f027806b865`.
- Mutación M2, retirar ambos campos del contrato: la prueba falló con `KeyError: 'idle_shrink_enabled'`. SHA-256 antes/restaurado `5b82cdd0b5ddbe19ae6c87e73d7d640b5885c5166dde99b626c860e6e77417b1`; mutado `00446ec938a4ec654a5f02872c993794613f0ca07c5ddf3c3a189c87a16f5be3`.
- Mutación M3, quitar la advertencia de la vista "Revisar plan": la prueba falló al no encontrar el texto después de mostrar ese diálogo. SHA-256 antes/restaurado `33e2055b798d293b5e2636637bce70d19c83256656c7922b7f7651ee6af7c806`; mutado `a5f7de24f12c3db15bb6398c3a9fc99dc1d79115eb5bc7975f7a57a45f6ad30d`.

No verifiqué el efecto en un monitor/grid activo; la comprobación de M1 y del diálogo usa TestClient y datos simulados. No ejecuté suites completas.

## Archivos

`api/routes/grid_control.py`, `grid/control_service.py`, `api/routes/grids.py`, `frontend/grids.js`, `tests/test_grid_control_api.py`, `tests/test_grid_level_adjust_summary.py`, `tests/ui/test_grids_browser.py`, `REPORTE_FASE23C.md`.
