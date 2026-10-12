# Reporte Fase 29c: barra de salud, navegación móvil y activos locales

## A. Barra de salud

**HECHO.** Se conserva `#api-chip` y sus clases `online`/`offline`; el texto accesible y el `title` explican el estado (`frontend/app.js:131-139`, `frontend/index.html:18`). Se añadieron indicadores separados para monitor, posible reinicio de Testnet y última alerta (`frontend/index.html:18`, `frontend/phase29c.css:1-10`). Un fallo de endpoint deja gris solo su indicador y la carga continúa con los demás (`frontend/app.js:185-199`). Las solicitudes protegidas pasan por el `ApiClient` existente, en secuencia, para compartir el aviso de token de la pestaña.

**HECHO.** El monitor usa `last_run.finished_at` o `started_at`. Con `gap_minutes_configured` válido, verde significa menos de 2 intervalos, ámbar de 2 a 4 y rojo más de 4; `FAILED`, `FAILURE`, `ERROR`, `PARTIAL` e `INTERRUPTED` son rojos con cualquier antigüedad (`frontend/app.js:163-175`). El endpoint entrega `gap_minutes_configured` (`api/routes/grid_status.py:200-214`). Si falta, se usan 900 segundos por intervalo, según el valor por defecto del prompt.

**HECHO.** `testnet_reset_suspected=true` muestra el aviso rojo. La última alerta usa `GET /api/alerts/status`, que responde `last_sent`; la ruta `/api/alerts` sin `/status` no existe (`api/routes/alerts.py:20-48`). Muestra su clave y antigüedad o “sin alertas”. El refresco ocurre al iniciar y cada 60 segundos; la navegación no registra otro temporizador (`frontend/app.js:185-199,387`). Los valores de API se escapan con `esc()` antes de insertarlos en HTML (`frontend/app.js:151-157,177-183`).

**Pruebas.** `tests/ui/test_fase29c_health_navigation_assets.py:24-90,117-159` cubre estados, fallo independiente de endpoints, reinicio Testnet, solicitud de token, escape de alerta, refresco y ausencia de hosts externos. La mutación del umbral pasó de SHA-256 `4d48c17fdcf7a413ef227edf4272eeb3f764cd7fe596746a0c36c4eb0e6e5d8c` a `744cb98255b4c5ee6a0162d1d6df01e7628736e269f555c60486b565b1392ffa`; la aserción de estado verde falló como se esperaba y la restauración volvió al SHA original.

## B. Navegación móvil

**HECHO.** Hasta 600 px se muestra un botón con `aria-expanded` y `aria-controls`, y un panel con las ocho rutas, sus nombres y la ruta activa. El menú se cierra al elegir una ruta, tocar fuera o pulsar Escape; Escape devuelve el foco al botón. Botón y enlaces tienen al menos 44 px de alto y un contorno de foco visible (`frontend/index.html:18`, `frontend/app.js:201-222,386`, `frontend/phase29c.css:12-28`). Los ocho enlaces de escritorio y sus `data-route` permanecen intactos.

**Prueba y mutación.** `tests/ui/test_fase29c_health_navigation_assets.py:93-115` verifica rutas, estados ARIA, selección, cierre y objetivos táctiles. Al quitar el enlace móvil Scanner, la prueba falló en la aserción de ocho enlaces. SHA-256 de `frontend/index.html`: antes y restaurado `418ce3e4e6daf59fabfc20ff2e80325977e3ccf6b45715bbfc8b1d93526761ae`; mutado `d94adf6854ab72c6fd8ad740d85301e50d07e66ce72fc54ceda05ce342dcf261`.

## C. Activos locales

**HECHO.** `frontend/index.html:7-8` carga `frontend/vendor/fonts.css` y Lightweight Charts localmente; se eliminaron referencias y preconexiones CDN. `api/main.py:263` monta `frontend/` como estático, y la prueba de navegador cargó el chart y ambas fuentes WOFF2 sin solicitudes externas (`tests/ui/test_fase29c_health_navigation_assets.py:148-159`). Las fuentes variables cubren Inter 400–700 y JetBrains Mono 400–700, con `font-display: swap` (`frontend/vendor/fonts.css:1-14`). Se incluyen las licencias Apache-2.0 y SIL OFL 1.1.

La descarga de `lightweight-charts@4.1.3` se verificó con el SHA-512 publicado en npm: `SJacmEyx3LmT2Qsc7Kq7cEX7nEHtQv0MOlujhRlcDxhW62pG6nkBlcM52/jNqkq8B28KQeVmgOQ7zrdJ4BCPDw==`. Fuentes y licencias se obtuvieron de árboles oficiales fijados a los commits indicados. SHA-256 y origen de cada archivo de `frontend/vendor/`:

| Archivo | SHA-256 | Origen |
|---|---|---|
| `frontend/vendor/lightweight-charts.standalone.production.js` | `0c146b9bfbc6e5f19da1a0de876dae43023c332d5157d82f7575f9b7653ee519` | [npm lightweight-charts 4.1.3](https://registry.npmjs.org/lightweight-charts/-/lightweight-charts-4.1.3.tgz), `package/dist/lightweight-charts.standalone.production.js` |
| `frontend/vendor/LICENSE-lightweight-charts.txt` | `70c9d5382506dd184465425c08a99ad9bd6d9ac1313c252968ba0b585e5ef823` | [npm lightweight-charts 4.1.3](https://registry.npmjs.org/lightweight-charts/-/lightweight-charts-4.1.3.tgz), `package/LICENSE` |
| `frontend/vendor/fonts.css` | `76cb6d0b079d884504338c5c84f4a077c1ec14127ad2d48a8933e538c4591713` | CSS local para estos activos |
| `frontend/vendor/fonts/InterVariable.woff2` | `693b77d4f32ee9b8bfc995589b5fad5e99adf2832738661f5402f9978429a8e3` | [rsms/inter, commit `353b61b9f4430d5f420d56605a6e7993e0941470`](https://raw.githubusercontent.com/rsms/inter/353b61b9f4430d5f420d56605a6e7993e0941470/docs/font-files/InterVariable.woff2) |
| `frontend/vendor/fonts/Inter-OFL.txt` | `262481e844521b326f5ecd053e59b98c8b2da78c8ee1bdbb6e8174305e54935a` | [rsms/inter, LICENSE.txt en el mismo commit](https://raw.githubusercontent.com/rsms/inter/353b61b9f4430d5f420d56605a6e7993e0941470/LICENSE.txt) |
| `frontend/vendor/fonts/JetBrainsMonoVariable.woff2` | `31ec365b93e4bad6f202ce23352a56d01ca4462b2afc782ed2cf6fa42ca9ac0e` | [JetBrainsMono, commit `19371302b95d218af43299bce79ddbddd0bc364d`](https://raw.githubusercontent.com/JetBrains/JetBrainsMono/19371302b95d218af43299bce79ddbddd0bc364d/fonts/webfonts/JetBrainsMono%5Bwght%5D.woff2) |
| `frontend/vendor/fonts/JetBrainsMono-OFL.txt` | `78052cf9d914eeaf3c14221584a00feeab4e158a7f34681c05da53fa01de5221` | [JetBrainsMono, OFL.txt en el mismo commit; se quitaron espacios finales y l?neas vac?as finales](https://raw.githubusercontent.com/JetBrains/JetBrainsMono/19371302b95d218af43299bce79ddbddd0bc364d/OFL.txt) |

**Prueba y mutación.** `tests/ui/test_fase29c_health_navigation_assets.py:168-184` compara el SHA-256 de cada archivo con el inventario esperado y verifica que no falte ni sobre ningún archivo. La prueba de referencias locales detectó la mutación que reintrodujo CDN; el archivo se restauró byte por byte. SHA-256 de `frontend/index.html`: antes y restaurado `418ce3e4e6daf59fabfc20ff2e80325977e3ccf6b45715bbfc8b1d93526761ae`; mutado `1d9e47db0800c3c9aa569c20085d0438470a1cb4c5b9fca980e6dc0a1aa9492`.

El tamaño total de `frontend/vendor/` es **647 325 bytes**.

## Correcciones de pruebas durante la continuación

1. `tests/test_frontend_encoding.py:15-26,33-42`. **Antes:** el recorrido incluía el JavaScript minificado de terceros y el ternario `t?v` causaba un falso positivo. **Después:** se excluye solo `frontend/vendor/`; la whitelist y la regex permanecen iguales. Una mutación cambia `señal` a `se?al` en un archivo temporal propio de `frontend/` y comprueba que el detector lanza `AssertionError`.
2. `tests/ui/test_models_page.py:579-581`. **Antes:** el test cargaba el bundle local después de `add_init_script`, sobrescribiendo su mock. **Después:** una ruta de Playwright responde JavaScript vacío al bundle antes de navegar. `git grep -n "LightweightCharts" tests/ui` encontró otros usos en Battle y Modelos, que instalan los mocks después de navegar, y en `test_frontend_format.py`, que usa un contexto JavaScript aislado. Esos casos pasaron y no se cambiaron.
3. `tests/ui/test_fase29c_health_navigation_assets.py:168-184`. **Antes:** no había prueba que detectara alteraciones de activos locales. **Después:** se calcula SHA-256 de todos los archivos de `frontend/vendor/` y se compara con el manifiesto esperado.

## Verificación

- `node --check` pasó para los seis archivos `frontend/*.js`: `app.js`, `battle.js`, `cuenta.js`, `grids.js`, `models.js` y `scanner.js`.
- Suite única de esta continuación: `$env:ASPLE_OFFLINE="1"; .\venv\Scripts\python.exe -m pytest tests -m "not live" -q --basetemp=.pytest-29c-final` — **1338 passed, 0 failed, 1 skipped, 18 deselected, 50 warnings**, 279.32 s.
- Capturas fixture de 1440×1000 y 390×844 antes y después en `.pytest-29c-shots/before/` y `.pytest-29c-shots/after/`; se inspeccionaron visualmente. La captura móvil posterior muestra el menú abierto con las ocho rutas y los indicadores de salud. Son datos de fixture.
- La auditoría de texto confirmó UTF-8 válido, LF, sin BOM ni U+FFFD. `git diff --check` está limpio. La prueba de encoding cubre los archivos propios y excluye solo vendor. El escaneo de patrones de secretos no encontró coincidencias.
- No se cambiaron `id`, textos de ancla ni `data-route` de escritorio.

**NO VERIFICADO:** datos de S1/Testnet, operación con una API real y dispositivo móvil físico. Las pruebas de navegador usan servidor local y datos fixture; no se ejecutaron pruebas live.

## Archivos incluidos

- `frontend/app.js`, `frontend/index.html`, `frontend/phase29c.css`
- `frontend/vendor/fonts.css`, `frontend/vendor/LICENSE-lightweight-charts.txt`, `frontend/vendor/lightweight-charts.standalone.production.js`
- `frontend/vendor/fonts/InterVariable.woff2`, `frontend/vendor/fonts/Inter-OFL.txt`
- `frontend/vendor/fonts/JetBrainsMonoVariable.woff2`, `frontend/vendor/fonts/JetBrainsMono-OFL.txt`
- `tests/test_frontend_encoding.py`
- `tests/ui/test_models_page.py`
- `tests/ui/test_fase29c_health_navigation_assets.py`
- `REPORTE_FASE29C.md`
