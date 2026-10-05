# Fase 18B-3 — discrepancias y límites

## Diferencia de alcance prevista por el prompt

- El preview del Scanner no incluye sigma de 24 h. Por ello la probabilidad de tocar piso/techo se presenta en Grid Advisor, pero no se añade al diálogo del Scanner ni se amplía el contrato del endpoint.

## No verificado

- No se ejecutó Binance ni Testnet y no se comprobó el comportamiento con saldos, filtros o comisiones reales. El aviso de polvo sigue siendo estimación pesimista.
- No se hizo inspección visual manual en un navegador real con la biblioteca del gráfico cargada. Los hosts CDN están bloqueados en el fixture de Playwright; las pruebas cubren datos, configuración y DOM disponible.
- No se validó que todos los símbolos dispongan de velas para simular. La API devuelve el motivo real de una excepción; la cobertura aquí usa stubs/datos locales.
- La probabilidad de tocar barreras usa retornos normales, deriva cero y volatilidad constante. No se calibró contra resultados futuros y la extrapolación a 72/168 h no está validada.
- No se midieron polvo efectivo ni umbrales de minQty/minNotional en Testnet.

No se encontró una discrepancia que requiera cambios fuera de L1–L10. `grid/engine.py`, `grid/monitor.py` y `grid/policy.py` conservan diff vacío.
