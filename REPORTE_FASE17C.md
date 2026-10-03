# Fase 17C — Scanner y formulario de apertura

## Resultado

Se añadió el preview de estructura y la pantalla Scanner. La ruta de apertura existente conserva su flujo de `dry_run` y confirmación Testnet. No hubo commits, push, llamadas a Binance/Testnet ni lectura de `.env` en las pruebas.

## Cambios por tarea

- **Evaluador compartido:** `grid/structure.py:27-54` extrae `evaluate_levels`; `suggest_structure` lo invoca en la selección y el caso mínimo (`grid/structure.py:117-129`). Conserva las ecuaciones del evaluador anidado.
- **Preview de solo lectura:** `api/routes/grid_structure.py:20-42` define el esquema cerrado; `:143-233` valida símbolo/autorización, reutiliza `grid_scan_service._market` y devuelve variantes y origen público. Los fallos al leer mercado responden 503 sin incluir excepción ni traza (`:154-159`). El test compara forma, cálculo, variantes inviables, ciclo teórico, autorización y que no se escriba en DB.
- **Variantes documentadas en respuesta y aquí:** `balanced` usa `suggest_structure` con `k_width` solicitado o 2.0; `wide` usa `k_width=max(3.0, balanced+1)` y el menor n con celda válida y margen bruto mínimo de 0.1%; `dense` conserva el rango balanced y usa el mayor n que satisface margen bruto mínimo de 0.1% y celda, ignorando explícitamente el polvo al elegir n. Cada variante inviable permanece en la respuesta con sus motivos. Ver `grid_structure.py:188-232`.
- **Márgenes:** bruto = separación − 2 × comisión; polvo estimado y margen después del polvo salen de `evaluate_levels`. La respuesta rotula el polvo como “estimación conservadora, no medida”.
- **Filtro del spread y aviso:** un spread menor o igual a `tick_size` pasa con “spread de 1 tick (mínimo posible)”; el resto usa el límite bps (`grid/scanner.py:55-76`). Los resultados con margen neto menor a 0.1 reciben `edge_warning` (`:126-134`). Pesos y umbral no cambiaron.
- **FastAPI:** router registrado con dos líneas netas en `api/main.py:23,151`.
- **Pantalla Scanner:** `frontend/index.html:13,18,24,27`, ruta `frontend/app.js:114`, contenido y peticiones en `frontend/scanner.js:18-43`, estilos en `frontend/scanner.css:1`. Usa `/api/coins`, `/api/grids/scan`, `/api/grids`, `/api/grids/structure-preview` y `/api/grids/open`. Errores permanecen en paneles propios. `X-API-Token` solo se lee de `window.gridApiToken`. El preview de apertura va primero con `dry_run=true`; la confirmación envía `dry_run=false, confirm=true`, rango y niveles explícitos. Se bloquea el doble clic y la UI no declara apertura ante error/estado parcial.
- **Polvo del grid:** `frontend/grids.js:326-338` muestra polvo medido, valor, ciclos, polvo por ciclo y fracción de celda cuando hay datos; ante campos faltantes muestra “no disponible”. No se tocaron endpoints ni proyecciones de estado.

## Evidencia y limitaciones

- Ancla de preview: sigma 0.035435, mid 1.4912, capital 100, fee 0.1%, step 0.1 supuesto de prueba. La estructura balanced conserva 9 niveles, separación aproximada 1.576% y margen neto aproximado 0.034%. El step 0.1 no está verificado en Testnet.
- El endpoint de scan omite el componente `cost_headroom` para resultados no elegibles. En esas filas la tabla muestra “no disponible” para margen bruto; no se supone una comisión por cliente. En las filas elegibles se obtiene la comisión reportada por el servidor.
- El polvo del grid se presenta según los valores de `grid_status`; la fracción de celda requiere `n_levels`, presente en la respuesta de detalle.
- La interfaz se verificó por fuentes, tests estáticos y `node --check`; no se hizo prueba visual en navegador. Playwright no está instalado en el venv.

## Mutaciones

Se ejecutaron nueve mutaciones temporales, restauradas inmediatamente y verificadas contra los bytes originales: omitir polvo del margen, confirmar con `dry_run=true`, quitar `confirm`, aceptar dos ticks, alterar la normalización efectiva de pesos, quitar `_authorize`, omitir polvo en `evaluate_levels`, ocultar el margen tras polvo y cambiar el plazo por defecto. **9/9 murieron** con las pruebas enfocadas.

## Verificación final

- Pruebas enfocadas: 37 passed.
- Suite offline completa: pendiente de ejecución final.
- `node --check frontend/scanner.js` y `node --check frontend/app.js`: correctos.
- Informes: UTF-8 sin BOM, sin CR y sin U+FFFD; el patrón exigido `[A-Za-z][?][a-z]` devuelve 0.
- No se probó flujo en navegador real ni una apertura Testnet.

## Diff por archivo

El conteo por archivo se adjunta tras la verificación final en esta misma sección.
