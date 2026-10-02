# Discrepancias de alcance — Fase 16

## Bloqueo de cableado API y scheduler

La lista autorizada permite modificar `main.py`, pero en este checkout ese archivo es un script CLI de predicción (`main.py:1-14`) y no crea ni configura una aplicación FastAPI. La aplicación real, su `lifespan`, la construcción de `app.state` y el registro de routers están en `api/main.py` (`api/main.py:1-4,27-29,114-119`). El router `/api/grids/scan` y `/api/grids/open` no puede quedar registrado en la aplicación desplegada editando el `main.py` permitido.

El auto-open también requiere registro de un job con el scheduler existente. El ciclo de vida y el arranque/parada de los loops están en `api/main.py:27-111`; no existe un archivo `scheduler` en la lista autorizada donde cablear este job.

La regla 1 de Fase 16 ordena detenerse y reportar si se necesita tocar un archivo no listado. Por ello, no se inició la implementación: agregar un router aislado sin registrarlo no cumpliría las tareas de API ni de cableado mínimo, y editar `api/main.py` o un módulo del scheduler excedería el alcance autorizado. No se hicieron cambios de producción ni se ejecutaron llamadas de red o pruebas.

## Cambio de alcance necesario

Autorizar explícitamente `api/main.py` para registrar el router y el servicio en `app.state`, y para registrar el job opcional de auto-open en el ciclo de vida existente. Si se prefiere que el scheduler quede en un módulo independiente, incluir también el archivo de scheduler elegido en la lista. La ejecución del auto-open debe continuar desactivada por defecto.

## Resolución

Ramón autorizó `api/main.py` con alcance limitado a router, `app.state.grid_engine`, `app.state.testnet_client`, servicio público y arranque/parada del job de auto-open en el `lifespan`. El archivo raíz `main.py` permanece intacto; la implementación se realiza conforme a esa autorización.

## Límite de parámetros de objetivo en estrategia simple

`GridEngine.create_grid` permite `max_days` en estrategia simple, pero rechaza parámetros `target_pct` y `target_usdt` para simple (`grid/engine.py:269-285`). `grid/engine.py` no está en el alcance autorizado de Fase 16. La API da 422 claro para ese caso y los objetivos se pueden usar con `strategy=smart`; auto-open también permite elegir smart por setting. No se modificó ni se rodeó la validación del motor.


## Resolución Fase 16-2

La auditoría encontró que el cliente público entrega `bidPrice`/`askPrice` como strings y el servicio ahora los convierte a `Decimal`; además, la apertura real requiere los valores reenviados del dry-run. Se añadió cobertura del cliente público sintético, la ejecución del plan y mensajes de error del motor. No hubo llamada real a Binance/Testnet ni cambios a `grid/engine.py` o al `main.py` raíz. Verificación final: 26 pruebas enfocadas pasaron; 8/8 mutaciones exigidas murieron y se restauraron byte por byte; suite offline completa: 560 passed, 18 deselected, 7 warnings. `api/main.py` está en LF y ambos diff stats son 63 líneas. No queda discrepancia funcional conocida dentro de este alcance.
