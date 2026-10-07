# Fase 20D-1b — cierre

## Preflight
HEAD observado: f721fcb, esperado. Los cambios previos de models/saved, el backup y .pytest_tmp se conservaron.

## 1. Battle
(a) Prueba sola: 1 failed, 2 warnings in 6.12s.
(b) tests/ui/test_grids_browser.py: 1 failed, 36 passed, 2 warnings in 25.89s.
(c) tests/ui/test_models_page.py seguido de tests/ui/test_grids_browser.py: 1 failed, 55 passed, 2 warnings in 54.53s.

En las tres, se esperaba No validado y se recibió literalmente: No se pudo cargar Battle de Modelos: HTTP 404. Puedes reintentar cuando el servicio responda.
URL observada en (a): http://127.0.0.1:62940/api/models/page-context?symbol=XRPUSDT&interval=1h. Puerto dinámico. frontend/battle.js:121 solicita la ruta; tests/ui/conftest.py:112-119 no monta models_status; api/routes/models_status.py:102-108 define page-context. El fallo aparece en (a) y (b), no solo después de Models. Los cuatro diffs contra HEAD no vinculan el 404 con los cambios 20D-1. No se corrigió fuera de alcance.

## 2. Reporte UTF-8
REPORTE_FASE20D1.md reescrito con Python y encoding UTF-8. Conteo de bytes ASCII ?: 2; todos son separadores ? de query URL, no reemplazos de tildes. Bytes U+FFFD: 0.

## 3. Contadores n
model_stats.py:218 cuenta filas verificadas maduras para estado de muestra; api/routes/volatility.py:282-283 también exige fuente vivo y elegibilidad antes de peso_actual.
model_stats.py:246 mide suficiencia de filas por grupo de dispersión.
model_stats.py:268 controla bias_alert.
No se cambiaron porque no son el conmutador val→vivo. La compuerta usa n/H en model_stats.py:137,166.

## 4. C3
frontend/models.js:139 presenta la nota de error en log y no cobertura del precio. tests/ui/test_models_page.py:25 comprueba texto y estilo calculado. No se cambió frontend/models.js. UI focal: 20 passed, 2 warnings in 11.72s.

## 5. SHA y exclusiones
tests/ui/test_models_page.py antes: 110c73915724979fc0408202a1e69b6e22643428b8aefeb62b880d85b81a7da4; después: f1ae3e1afc51db5cdc2042ef6b6770053f1f6d14e1e3ef284187f5b614f241c7.
REPORTE_FASE20D1.md antes: b46caeb84bd3155b78590d9078fc7ff74a04ea9cfdb1674a02373a808f4fb9e5; después: 5bbc28e1af8c26329647b770326b448c53805a36f82705da4779bc8678cbf39b.
REPORTE_FASE20D1b.md se creó en este cierre; SHA final se informa al usuario.
.pytest_tmp/20d1-python no figura en git ls-files, no se stageó ni agregó ni limpió. Sin stage, commit ni push.
NO VERIFICADO: page-context en servidor activo; sin pruebas live ni Testnet.

Archivos para commit posterior: REPORTE_FASE20D1.md, tests/ui/test_models_page.py, REPORTE_FASE20D1b.md.
