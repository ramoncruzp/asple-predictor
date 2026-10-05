# Fase 18B - Discrepancias y limites

## Limites conocidos

1. **Credenciales Testnet:** `api/routes/grid_account.py:125` devuelve que se reinicie el servidor. Se mantiene asi porque el cliente y workers se crean en el `lifespan` (`api/main.py:81-119`); no se intento reconstruirlos en caliente. Se prohibe usar Testnet real en esta fase.
2. **Grafico Dashboard:** fixtures Playwright bloquean `cdn.jsdelivr.net` y `fonts.googleapis.com`, por tanto la interaccion visual con la libreria externa no se verific-. Se verificaron etiquetas temporales y huecos mediante la prueba Node de `tests/ui/test_frontend_format.py:95`.
3. **Modelos B/C:** no se midi- tiempo ni memoria con sus artefactos de produccion. El modo sombra se cubre con stubs para artefactos inexistentes y errores de carga (`tests/test_shadow_model_loading.py`). D/TFT sigue fuera del alcance aprobado.
4. **Reglas de exchange:** el minimo tipico de 5 USDT y el comportamiento real de filtros/credenciales Testnet no fueron probados contra el exchange. El texto de Advisor lo etiqueta como no verificado.
5. **Cuenta:** la duplicacion de resumenes con la pestana Grids permanece porque eliminarla requeriria reestructurar el flujo de datos/UI; se dej- como permitia E5.
6. **Seguridad:** la ruta de credenciales se prueba con un archivo temporal configurable. No se lei ni escribi el `.env` real. `.env.bak` se ignora mediante `.gitignore:7`.

## Alcance de cierre

No se cambio codigo de cierre, liquidacion, PROFIT_CLOSE ni reposicion. `git diff --quiet -- grid/engine.py grid/monitor.py` termino sin diferencias. No se hicieron llamadas reales a Binance/Testnet ni commit/push.
