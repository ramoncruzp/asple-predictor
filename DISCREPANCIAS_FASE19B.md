# Discrepancias — Fase 19B

- `TestClient` no puede ejecutarse en el venv: falta `httpx` (Starlette lo requiere). Para no agregar dependencias, se probó el contrato de la función de ruta `model_stats` directamente y el render real con Playwright y respuestas interceptadas; la serialización HTTP ASGI no quedó verificada.
- El histórico local observado abarca aproximadamente siete días y menos de 30 resultados maduros por modelo/horizonte. Por diseño, las métricas están en acumulación, los pesos efectivos son VAL y ninguna salida queda `validated`.
- La mutación N_MIN también encontró una asercón vieja del test de Grid Advisor fuera de su función, desplazada al insertar el caso UI. Se devolvieron esas dos aserciones a su test original; el nuevo test Playwright pasó después. No queda fallo conocido.
- Sin evidencia de meses, carga CPU de largo plazo, latencia del scheduler ni servicio real de Binance/Testnet. No se entrenaron/reentrenaron modelos ni se alteró la política del grid.
