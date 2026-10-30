# Discrepancias Fase 43

- El texto de la instrucción señalaba mojibake en mensajes de `grid/meta_operator.py`. La inspección de bytes confirmó que los mensajes fuente ya estaban en UTF-8 válido, así que no se reescribieron ni se alteró su conducta.
- `--lista-inicial` resta las monedas activas del Coin Registry si `asple_predictor.db` existe; sin esa BD local emplea la lista fija de datos. No se abrió la BD real durante el desarrollo ni las pruebas.
- El presupuesto del sincronizador limita la espera del hilo de trabajo a 60 s; el cliente existente conserva su política de reintentos. Si una operación en curso excede el límite, su hilo daemon puede terminar después del resumen, y el cursor persistido permite retomar desde la última vela escrita.
- La verificación API/BD es una herramienta optativa que requiere acceso público a Binance cuando se ejecuta con `--comparar-api`; en esta fase solo se ejercitó con cliente falso.
- No se ejecutó en S1 ni se usaron Testnet, `.env`, red o BD real. El archivo `market_data.db` tampoco se generó ni se añadió al repositorio.
