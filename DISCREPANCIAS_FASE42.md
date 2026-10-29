# Discrepancias — Fase 42

## C34 y C35

- Sin diferencias funcionales registradas. C34 enlaza la propuesta de asignación ampliada con las etiquetas futuras; C35 procesa transiciones sin depender de que alguien abra una pantalla.

## 42a

- El replay usa ventanas de siete días separadas por siete días y una caché LRU de 512 entradas. Presupuesto: 60 segundos por job. Una simulación en curso no se interrumpe; el presupuesto se revisa entre alternativas y grids.
- El inventario retenido permanece fijo por rama y se valora al cierre de cada vela como aproximación al bid, menos 0,1 % de comisión. No se midió contra un libro de mercado real.
- El bot usa las alternativas de respaldo −0,5 %, −1,5 % y −3 %. No hay señal de toque específica conectada para grids normales en esta fase.

## 42b

- Se usa una etiqueta real a siete días. No hay muestra local de mercado real; el medidor queda "Sin datos suficientes" hasta acumular 30 etiquetas.
- El intervalo es bootstrap pareado determinista de diferencias por recomendación. No se declara evidencia suficiente si no hay 30 etiquetas o el intervalo incluye cero.

## 42c

- El camino de aplicación se probó con exchange falso/fixtures; no se ejecutó contra Testnet ni mercado real. El chequeo de valoración conocida usa el libro Testnet al aplicar.
- Pausar compras no liquida inventario ni altera ventas vivas. La pausa se despeja si ruptura v2 y volatilidad caen bajo sus umbrales.
- El interruptor queda apagado por defecto. La prueba confirma que apagado no llama al aplicador. No se hizo un diff de estado de órdenes real porque no se iniciaron servicios de mercado; 42a/42b son lectura/replay y 42c no actúa sin opt-in.

## Pendiente de evidencia operativa

- En S1, consultar en modo solo lectura duración, grids incluidos/excluidos, motivos y etiquetas maduras. No activar interruptores en esta fase.

## C38a — Limitación de la medición sintética

La base sintética alcanzó las cantidades solicitadas para muestras, etiquetas Meta, ledger y grids, pero su JSON de señales fue mínimo y no tenía etiquetas de recomendaciones de grids. Por ello midió el costo de filas y agregados, pero no reprodujo toda la cardinalidad de bootstrap ni el tamaño de los bloques de señales de S1. La opción conservadora es no cambiar cálculos ni decisiones a partir de esta medición; C38b atenderá las recalculaciones repetidas y C38e medirá por candidato con velas sintéticas.


## C38b - Instantánea y endpoints

- La medición sintética directa fue 1,09 s, menor que el umbral de logging de 1 s por variación local, por lo que la prueba C38a ahora exige `INTELLIGENCE_TIMINGS` solo cuando corresponde. C38b mide además el camino snapshot por debajo de 2 s.
- `/api/grids` mantiene lecturas vivas de estado/precio y no se garantiza bajo 2 s; servirlas desde snapshot podría mostrar efectivo o estado desactualizado. `/api/grids/{id}` usa snapshot solo para el bloque de inteligencia.
- La primera respuesta antes de terminar el cálculo devuelve una estructura básica `calculando`; no contiene agregados finales. Si el precálculo falla, se conserva la instantánea anterior y se registra el error.

## C38c - Memoria

- El límite de 16 MiB rechaza la instantánea nueva completa, mantiene la anterior y deja `last_error`; no recorta resultados para evitar alterar agregados.
- El límite de tamaño y su validación se incorporaron junto con el servicio de C38b; el commit C38c añade medición de diez reemplazos y prueba de conservación. La medición es sintética y local. No predice el RSS total del servicio ni mide el trabajo de todos los jobs de producción concurrentes.

## C38d - Concurrencia y paridad

- Las pruebas verifican concurrencia de GET en un proceso y restauración SQLite local; no simulan varios procesos Uvicorn. El single-flight es por proceso.
- El precálculo horario de C35 renueva la instantánea, y las peticiones manuales comparten el cálculo en curso. Un cliente puede seguir leyendo la instantánea anterior mientras se genera la siguiente.

## C38e - Presupuesto del job de grids

- El presupuesto se comprueba entre candidatos y ventanas; una simulación individual sin timeout interno no puede interrumpirse. Si esa simulación excede 45 s, las siguientes alternativas de ese grid no arrancan y se persiste la mejor recomendación parcial disponible (o `mantener` sin métricas si no terminó ninguna ventana).
- El job mantiene 60 s total y 45 s por grid. Al agotarse el total, persiste filas `mantener` parciales para los grids elegibles restantes sin empezar simulaciones, para no dejar ninguno sin registro. Estas filas indican que no se midieron ventanas y no deben interpretarse como recomendación con evidencia.
- El estudio con 180 días usó velas planas sintéticas y una geometría simple; los tiempos de cada símbolo y las exclusiones de producción deben confirmarse observando `GRID_INTEL_JOB_SUMMARY` en S1.
