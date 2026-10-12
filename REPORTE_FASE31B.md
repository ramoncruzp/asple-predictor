# Fase 31b: correcci?n de zonas horarias

## Cambios

- Se a?adi? `ASPLEFormat.parseApiTime(value)` en `frontend/app.js`. Las fechas ISO con hora y sin sufijo de zona se interpretan como UTC; `Z` y offsets expl?citos, incluido `-04:00`, conservan su significado.
- El parser compartido se usa en `elapsedLabel`, estado de monitor, actualizaci?n de edad de datos, fecha local de cambios globales, errores y eventos de grids y Laboratorio, y fechas de entrenamiento e historial de modelos.
- El monitor marca en rojo fechas m?s de 60 segundos en el futuro y muestra `reloj desajustado`; las fechas antiguas conservan la clasificaci?n por intervalo configurado.
- Laboratorio muestra una sola hora de cambio en la fila del interruptor; el estado anuncia `Ajuste global actualizado.`
- Se actualiz? la expectativa del men? m?vil de 8 a 9 enlaces, ya que Laboratorio se a?adi? como ruta en la Fase 31.

## Verificaci?n

- `Get-ChildItem frontend/*.js | ForEach-Object { node --check $_.FullName; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }`: todos los archivos JavaScript pasan.
- `$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest tests/ui/test_fase31b_timezone.py tests/ui/test_fase29c_health_navigation_assets.py tests/ui/test_laboratorio.py tests/ui/test_cuenta_settings.py tests/ui/test_grids_browser.py -q --basetemp=.pytest-31b-final --tb=short`: **77 passed**, 2 warnings deprecados de dependencias.
- Mutaci?n: se retir? temporalmente el sufijo `Z` en `parseApiTime` y se ejecut? `tests/ui/test_fase31b_timezone.py::test_api_time_parser_preserves_explicit_zones_and_assumes_utc_for_naive[America/Santo_Domingo]`. Fall? como se esperaba: la fecha naive qued? desplazada cuatro horas. `frontend/app.js` se restaur? byte por byte (SHA-256: `db439ac0fd3a10da32827d70acf8d7ce06b7d828eea17f48e83e90c95072b36e`).
- Verificaci?n adicional del test previamente obsoleto: `$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest tests/ui/test_fase29c_health_navigation_assets.py::test_mobile_menu_has_nine_routes_and_closes_accessibly -q --basetemp=.pytest-31b-menu-audit --tb=short`: **1 passed**.
- Los seis casos nuevos ejercitan naive, `Z`, `-04:00`, edad de 5 minutos, antig?edad de 2 horas y reloj futuro, tanto en `America/Santo_Domingo` como en `UTC`.

## Auditor?a

- `git diff --check`: sin errores.
- Archivos nuevos/modificados guardados como UTF-8 sin BOM y con LF; sin U+FFFD ni patrones de `?` usados como sustituto de letras acentuadas.
- B?squeda de secretos en los cambios: sin credenciales ni tokens literales.
- `git grep -n -E 'Date\.parse|new Date\(' -- 'frontend/*.js' ':!frontend/vendor/**'`: las fechas de respuestas API usan el parser compartido. Las restantes construcciones son reloj local, fechas num?ricas del gr?fico o fallback de hora actual.
