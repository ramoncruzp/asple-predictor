# Reporte Fase 20B-V — Entrenamiento de volatilidad

## Estado por punto

| Punto | Estado | Evidencia | Límite de verificación |
|---|---|---|---|
| V1 descarga segura | HECHO | `scripts/download_candles.py`: descarga a `.tmp`, filtra velas cerradas y usa reemplazo atómico; borra el temporal si falla. Prueba con cliente simulado. | No se llamó a Binance ni se verificó una descarga real. |
| V2 refresco y entrenamiento | HECHO | `scripts/train_vol_models.py`: refresco aislado en `--candles-dir`, 730 días, mínimos de filas y cierre reciente (<=2 h), fases de progreso y limpieza `.tmp` al fallar. Las pruebas simulan datos y entrenamiento. | No se ejecutó entrenamiento real; tiempo real desconocido. No se modificaron manifiestos, consensos ni artefactos reales. |
| V3 trabajos | HECHO | `models/training_jobs.py`: comando `vol`, 730 días, progreso de descarga, limpieza de temporales y rechazo de cancelación durante `guardando`. | `taskkill` real de un trabajo de volatilidad en Windows NO VERIFICADO. |
| V4 API | HECHO | `api/routes/model_training.py`: acepta solo `models=["vol"]`, exige confirmación, XRPUSDT, 1h y 730 días; mezclas inválidas y trabajo activo responden 422/409. | Verificado con FastAPI TestClient y servicio simulado, sin proceso de entrenamiento real. |
| V5 artefactos | HECHO | `api/routes/models_status.py`: endpoint `/api/models/vol/artifacts` informa tiempos de disco/cargado, rango del manifiesto y metadatos/hash corto del consenso. | El esquema actual de `vol_forecasts` no tiene un campo de versión del modelo. No se inventó uno. La disponibilidad real del predictor se probó con fixture. |
| V6 UI | HECHO | `frontend/index.html`, `frontend/models.js`, `frontend/models.css`: tarjeta de entrenamiento independiente antes de volatilidad, fila Volatilidad, confirmación específica, fechas y etiqueta del trabajo; controles y notas específicos por modelo. | Playwright cubrió disposición, contenido y estados de diálogo con API simulada; no se probó un entrenamiento real. |

## Pruebas

Comando focal ejecutado con `ASPLE_OFFLINE=1`, pruebas de UI al final y directorio temporal fuera del repo. Resultado literal:

```text
127 passed, 1 skipped, 22 warnings in 24.88s
```

La ejecución inicial no recolectó pruebas porque el patrón inexistente `tests/test_volatility*.py` se pasó literalmente a pytest. La ejecución final enumeró los archivos existentes `test_vol_*.py` y `test_model_stats*.py` y pasó.

Cinco mutaciones fueron detectadas por aserciones de comportamiento y cada archivo se restauró byte por byte:

| Mutación | Resultado | SHA-256 antes | SHA-256 mutado | SHA-256 restaurado |
|---|---|---|---|---|
| Quitar limpieza de temporales ante error | Detectada; test de preservación/limpieza falló | `36232ec8cc18fcbfd3ec1433ae138ac34d5ea5b3f4f660e8949b9b09ef3f49fb` | `5765b0fea75a99a7a99d7aba67985bf1d2858900f1847fa591c8c4a7608aabf1` | `36232ec8cc18fcbfd3ec1433ae138ac34d5ea5b3f4f660e8949b9b09ef3f49fb` |
| Desviar la carpeta aislada de velas | Detectada; test de rutas/hash falló. Mutación dirigida al directorio temporal padre para no escribir en `data/cache`. | `36232ec8cc18fcbfd3ec1433ae138ac34d5ea5b3f4f660e8949b9b09ef3f49fb` | `527fd146c042c8b61d0713f9b50c32a5fd96332eb4fe34249780bd2566e962b8` | `36232ec8cc18fcbfd3ec1433ae138ac34d5ea5b3f4f660e8949b9b09ef3f49fb` |
| Permitir mezclar Vol con A/B/C | Detectada; validación API falló | `83ce55e19bcdd172cb3532245fdd3079592905c306c24af6a54418b7f6692bbd` | `67bac6168ccf3c2a0299eca06eec8b8e2d6361927d12a2cce78b106b50044bb3` | `83ce55e19bcdd172cb3532245fdd3079592905c306c24af6a54418b7f6692bbd` |
| Permitir velas obsoletas | Detectada; validación de antigüedad falló | `36232ec8cc18fcbfd3ec1433ae138ac34d5ea5b3f4f660e8949b9b09ef3f49fb` | `a3c3fa6553e0f1f81afce4cc40e06c1936a66f9d5684c3636303dce122eeb035` | `36232ec8cc18fcbfd3ec1433ae138ac34d5ea5b3f4f660e8949b9b09ef3f49fb` |
| Permitir cancelar durante guardado | Detectada; test de cancelación protegida falló | `27440115a387410e5acc0c379a51de02d1153eab99f1f567dd4dffbe593df7ce` | `f8508cab80aba9907d2b7973b1b6f25457f4bb1edabf8eb8744c6e44ae7bf57d` | `27440115a387410e5acc0c379a51de02d1153eab99f1f567dd4dffbe593df7ce` |

Todas las mutaciones reportaron `restored_byte_identical=True`.

## Alcance y discrepancias

- No se hizo stage, commit ni push.
- No se llamó a Binance/Testnet ni se ejecutó un entrenamiento real.
- No se tocaron `models/saved/metrics_xrp_1h.json`, artefactos `.joblib`/`.pt` ni `logs/training/`. La prueba de refresco comprobó que los CSV de producción `data/cache/xrp_1h.csv` y `data/cache/xrp_5m.csv` mantuvieron sus SHA-256 durante la prueba simulada.
- El HEAD esperado en el prompt era `81325fb`; el checkout comenzó en `442779c`. Se continuó sobre el HEAD real, preservando los cambios ya presentes.
- La duración de entrenamiento, una descarga 5m real y la presencia de una columna `model_version` en `vol_forecasts` quedan NO VERIFICADAS; esta última columna no existe en el esquema consultado.


## Cierre 20B-V-fin: F1-F3

| Punto | Cambio y evidencia | Prueba |
|---|---|---|
| F1 | `frontend/models.js:163` ahora renderiza `Pocos datos` como `<strong class="models-few-data">`; `frontend/models.css:7` le da 13 px, color de aviso y bloque; `tests/ui/test_models_page.py:63` verifica tag, tama?o y color distinto del texto atenuado. | PASO en la ultima corrida UI. |
| F2 | `frontend/index.html:24` informa direccion XRPUSDT/1h y volatilidad 1/2/4/24 h, velas publicas sin llaves y reinicio para cargar artefactos nuevos. La mezcla de versiones se atribuye al entrenamiento de direccion. | `tests/ui/test_models_page.py:279` PASO. |
| F3 | `frontend/app.js:26` ya convierte el `detail` HTTP en `error.message`. `frontend/models.js:307` conserva el trabajo activo y muestra el error junto a el; al volver a renderizar, el boton Cancelar queda habilitado. | `tests/ui/test_models_page.py:287` con HTTP 409 PASO. |

Ultima ejecucion: `18 passed, 2 warnings in 11.34s`.

Mutaciones F1-F3, con reemplazo unico (`count == 1`); cada una fallo en su asercion conductual y el archivo se restauro byte por byte:

| Mutacion | Archivo/linea | SHA-256 antes | SHA-256 mutado | SHA-256 restaurado | Resultado |
|---|---|---|---|---|---|
| Revertir strong a small | `frontend/models.js:163` | `0db33e738d76b79f18b935d6c6432f58b72fb6f2cf67332a48613bf251bc4fa0` | `680d37e5c166f1f4711d1400a3f8c6825de2cb3239ca33166734fde12ba20974` | `0db33e738d76b79f18b935d6c6432f58b72fb6f2cf67332a48613bf251bc4fa0` | Detectada |
| Quitar reiniciar | `frontend/index.html:24` | `178637dcbba6624cfb87cb9999184f6aea96ffd9447fd8a014c4fe2effd16fa2` | `4ba76c8550afa540b19e2fa71a1842240a7fc8de1f465919ee74d69e12e5dadf` | `178637dcbba6624cfb87cb9999184f6aea96ffd9447fd8a014c4fe2effd16fa2` | Detectada |
| Ocultar detail del 409 | `frontend/models.js:307` | `0db33e738d76b79f18b935d6c6432f58b72fb6f2cf67332a48613bf251bc4fa0` | `009dc7674e5874ae588ec8dfe2bc4170f62eecd3e7fd31a955a49cc4cac2cab9` | `0db33e738d76b79f18b935d6c6432f58b72fb6f2cf67332a48613bf251bc4fa0` | Detectada |

SHA-256 de cada archivo cambiado por F1-F3:

| Archivo | Antes | Despues |
|---|---|---|
| `frontend/models.js` | `e232dd4f111b2a69eb724958913628085dcd3ece2f1e9e17bda8c8e204df12ef` | `0db33e738d76b79f18b935d6c6432f58b72fb6f2cf67332a48613bf251bc4fa0` |
| `frontend/models.css` | `9147abfd1b67ec3cbc8c4a9e253f9589111eb57459506cc6b9728fbe0cb19f7c` | `6fbb5f78e4b0be546d98df7d57f41b452b81ed893550c99756716e8db8a4a6fc` |
| `frontend/index.html` | `12cb86bd6ecc0b605f3e559dc44162222df7854b2376aeb1ade73d2c039acd75` | `178637dcbba6624cfb87cb9999184f6aea96ffd9447fd8a014c4fe2effd16fa2` |
| `tests/ui/test_models_page.py` | `52eb59cc99c3aa095cdabace920ae89bbb182d0b56996b531f92fa25ce516fd4` | `ab2483e310e982160c57610acea75f9b120f854b768ca0690e668219bd2d7266` |
| `REPORTE_FASE20BV.md` | `462bdc4d0ccce4d7ab90464318d5dff19e3f6204cfe0a3faca9faf96f3eecea1` | Report hash calculado despues de anexar este apartado; se informa en la respuesta de cierre. |
