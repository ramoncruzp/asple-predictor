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
