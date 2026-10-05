# Discrepancias — Fase 19C-1

1. El Advisor actual construye piso/techo con ATR y soporte/resistencia; no llama a `suggest_structure`. Se preservó esa base y el ajuste 1.25× solo se aplica a ese rango derivado y a L8.
2. El fallback actual de Advisor calcula desviación estándar de log-retornos horarios; el EWMA .94 citado en el prompt describe otra ruta y no se sustituyó.
3. La regla descrita como 200 resultados brutos ya no coincide con el código de 19B-2: se usa `LIVE_VALIDATION_MIN_EFFECTIVE=50`. `auto` consulta `validation_status_live` y exige `validated`, sin recalcular esa compuerta.
4. 19C-1 no conecta el consumo del consenso al motor Smart. La integración Smart queda fuera de esta autorización y no se ejecutó 19C-2.
5. El límite de ocho comandos de prueba se excedió por una mutación exploratoria adicional; quedó restaurada byte a byte. La mutación final que cambia sigma por bias fue detectada.
6. NO VERIFICADO: transición real a `validated`, resultados de grids en operación, precios o ejecución Testnet y beneficio predictivo.
