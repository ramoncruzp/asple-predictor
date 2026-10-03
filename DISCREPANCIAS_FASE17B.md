# Discrepancias — Fase 17B

## Límites observados

- La pantalla Cuenta obtiene el tipo de cuenta y canTrade mediante el endpoint privado de Testnet. Con credenciales ausentes, el bloque de balance responde null; las ganancias almacenadas en la base siguen disponibles.
- Se valoran como máximo veinte activos/precios por petición del resumen. Si se agota el presupuesto de consultas, los activos restantes se muestran sin valorar; no se les asigna precio cero.
- El plan repository clasifica como movibles las celdas SELL_OPEN con order_id. Las posiciones sin una orden de venta gestionada quedan cuantificadas como inventario no gestionado, siguiendo el comportamiento de GridEngine._close_repository.
- La vista previa de liquidación usa el bid disponible y calcula la comisión estimada al 0.1 %. El resultado real depende de fills y filtros de Testnet. No representa un precio garantizado.
- El porcentaje de capital por grid usa la suma del capital_total de los grids mostrados, incluidos grupos cerrados y repositorios.
- La prueba del merge en SQLite verifica preservación concurrente mediante dos hilos/conexiones y una aserción de una sola transacción de escritura por merge. No se ejecutó una prueba contra PostgreSQL.
- Playwright no se ejecutó. Los smoke tests nuevos verifican flujo y contenido de fuente; el test browser opcional previo continúa intacto.
- No se ejecutaron acciones ni lecturas contra Binance Testnet real. Las llamadas externas quedan limitadas a la configuración runtime del servidor y se probaron con clientes fake bajo ASPLE_OFFLINE=1.

## Funciones excluidas por soporte del motor

- Cambiar capital requiere reescalar celdas y saldos con write-ahead; no se implementa en un grid abierto.
- Cambiar interés compuesto o préstamos entre celdas modifica la contabilidad; queda fuera de la lista blanca de parámetros.
- Cambiar el número de bots sin mover el rango no existe como acción independiente. El número opcional solo se acepta junto con adjust y su preview.
- No se alteró grid/engine.py ni el esquema de base de datos.
