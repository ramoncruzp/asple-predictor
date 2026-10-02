# Fase 17A-4 — Monedas, salud de API y formato de precios

## Cambios

- Grid Advisor obtiene sus pares activos de `GET /api/coins`, igual que la pantalla Monedas. Conserva la selección entre lecturas, inserta `/` antes de `USDT`, escapa valores HTML y usa XRP/BTC/ETH/SOL como respaldo si falla la lectura de Coin Registry. El selector se actualiza al entrar a `#grid`, por lo que refleja altas y bajas realizadas desde Monedas.
- `/api/health` es una ruta `async` registrada antes del montaje de `StaticFiles`. Devuelve únicamente `status` y `uptime_s`; no consulta base de datos ni red. `api/main.py` queda en **21 líneas añadidas y 1 eliminada frente a HEAD (20 netas)**, incluyendo el cambio previo de registro del router de Grids.
- Middleware HTTP registra en `WARNING` con logger `asple.slow` las solicitudes sobre 3 s: método, ruta sin query, estado, duración en ms e hilos activos. No registra cabeceras ni cuerpos.
- El estado del chip consulta `/api/health` con timeout de 8 s. Dos fallos consecutivos lo dejan ámbar como «API lenta»; el tercero lo pone Offline. Un éxito restaura Online inmediatamente. El banner de desconexión informa cuánto pasó desde el último éxito. Los errores de Dashboard, Battle, Grid Advisor y Monedas se muestran en su panel y no modifican el estado global.
- `formatPrice` muestra 2–4 decimales desde 1 y cuatro cifras significativas bajo 1, expandiendo la notación científica. Se usa para precios y niveles del Grid Advisor, precios/rangos de volatilidad y la columna Precio de Monedas. Los importes de capital conservan `money`; `frontend/grids.js` no se tocó.

## Verificación

- Pruebas enfocadas: **6 passed**. Incluyen respuesta exacta de health con DB configurada a una ruta inexistente, ausencia de apertura de DB, log lento sin query string, opciones de Coin Registry/escape/selección, máquina de estados y formatos de precio.
- Mutaciones temporales detectadas y restauradas: **4/4** (umbral de histéresis, consulta de DB desde health, `setOffline(true)` desde el cargador de un panel, formato fijo de cuatro decimales). Los hashes de los archivos mutados coincidieron antes y después de cada mutación.
- `node --check frontend/app.js` pasó. Los tests de interfaz ejecutan funciones puras con Node/VM; no se probó en navegador el DOM real, el comportamiento visual del chip ámbar ni la navegación interactiva.
- Los tests ASGI no inician el `lifespan` completo del predictor; verifican que health y el middleware respondan sin inicializar ciclos de modelos, red o base.
- Suite offline completa: pendiente de ejecutar al cierre de esta fase.
- Los informes se escribieron como UTF-8 sin BOM y LF. Para detectar el signo literal entre letras se verificó `[A-Za-z][?][a-z]`; el patrón pedido `[A-Za-z]?[a-z]` interpreta `?` como cuantificador en GNU grep y no sirve para detectar sustituciones de tildes.

Sin commit, push, llamadas reales ni cambios en `frontend/grids.js`.
