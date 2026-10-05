# Discrepancias — Fase 18D

Las pruebas de dry-run existentes se actualizaron porque la API ahora informa `testnet_price`, `testnet_in_range` y `testnet_price_guard`; la lectura no disponible es válida y no debe rechazar la vista previa.
La validación usa `engine.exchange.get_book_ticker`, el mismo método de lectura del snapshot de `GridEngine`; no añade endpoints firmados ni acciones de escritura.
El precio Testnet real de PEPEUSDT y la apertura real siguen sin verificarse. No se tocaron `engine.py`, `monitor.py`, `policy.py` ni `grid/levels.py`.

## Complemento 18D

El nuevo guard requiere que el doble `RecordingEngine` del test de reutilización exponga `exchange.get_book_ticker`; se actualizó para devolver un libro simulado dentro del rango calculado. Esto mantiene la prueba sin red ni escrituras.
Los rechazos antes de crear el grid incluyen `range_low`, `range_high`, `n_levels` y el snapshot Testnet disponible en `GRID_OPEN_REJECTED`; si un lado vale cero, `mid` se registra como `null` porque no se calcula.
No se verificó si el libro de Testnet cambia entre la prevalidación y la lectura interna posterior del motor; el guard evita las condiciones observadas antes de invocarlo, y el motor conserva su validación de respaldo.
