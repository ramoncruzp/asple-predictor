# Discrepancias — Fase 40a

## Decisiones de alcance

- **D5 y el orden por fases:** no se marca como discrepancia. D5 describe el módulo final; esta entrega implementa solo el núcleo por instrucción expresa. El operador de análisis/decisión permanece para 40b y la pantalla Metas para 40c.
- **Bots individuales:** no existen actualmente como pieza independiente. El modelo admite el tipo `individual_bot_grid` enlazado a un grid de un nivel (`grid/meta.py:208`). La conducta de compra X/venta Y sin recompra se construirá en 40b; no se activó en 40a.
- **Reserva de capital:** al iniciar se verifica el saldo libre Testnet menos el capital comprometido por niveles ociosos de otros grids y se persiste la meta/capital reservado. Binance Spot no ofrece una subcuenta por meta aquí, así que esto no inmoviliza físicamente USDT frente a aperturas ajenas posteriores. La valoración sí permanece aislada por los enlaces de piezas.

## Límites que deben quedar visibles

- Si un fill histórico no trae el importe cotizado o comisión verificable, el núcleo intenta recuperarlos por orden/trades. Si la atribución aún no se puede comprobar, marca `DESCONOCIDO` y no toma ese valor como cero.
- Un plan de cancelación expira a los cinco minutos y vive en memoria del proceso; después de un reinicio se solicita otro plan. La confirmación compara la lista de piezas y no reutiliza un plan vencido.
- El núcleo permite ligar grids completos y exclusivos. El operador todavía no genera ni ajusta piezas, no presenta Scanner/Advisor y no crea grids automáticamente; eso queda dentro de 40b. No se añadió interfaz, que corresponde a 40c.
- Los tests usan exchange falso, precios simulados y `ASPLE_OFFLINE=1`. No prueban colocación real de órdenes ni despliegue en Testnet; esta fase no hizo llamadas a Testnet ni migró una BD real.

## Incidencia de pruebas durante la implementación

La primera corrida completa tuvo dos fallos en pruebas de `api/main.py`: sus `SimpleNamespace` de configuración no tenían `meta_mode_enabled`. La inicialización ahora usa `getattr(..., False)` para mantener esas configuraciones de prueba y entornos anteriores apagados. `tests/test_grid_monitor.py` pasó y la corrida final completa quedó verde. No se cambió comportamiento de grids ni sus defaults.

Una corrida posterior tuvo un fallo ambiental Windows en `tests/test_loan_cohorts_process.py`: el proceso hijo no podía abrir el lock file creado dentro de `tmp_path`. El fixture usa ahora la ruta temporal compartida que obtiene el mecanismo de lock a partir de la BD temporal. El archivo pasó con 7 pruebas y la corrida completa final quedó verde.
