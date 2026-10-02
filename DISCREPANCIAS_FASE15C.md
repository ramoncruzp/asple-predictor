# Fase 15C - discrepancias y limites pendientes

- **Prueba live:** NO VIABLE EN LIVE en este alcance. No se forzo un precio ni se cerro un grid real; cierre y reanudacion se cubren con tests/fakes. No se accedio a Testnet ni Binance.
- **Comisiones:** runtime estima 0.1% en venta para decidir/proyectar el objetivo; compra y fees reales se reconstruyen desde fills/trades cuando existen. La tasa efectiva puede variar por cuenta y mercado.
- **Estado CLI:** usa el ultimo snapshot persistido y evita consulta de mercado; puede estar desactualizado.
- **Equity contabilizada:** usa celdas propiedad del grid; excluye polvo y saldos no asignados del exchange. Inventario no vendido sigue el flujo HOLDING.
- **Estudio:** usa historico cacheado identificado por SHA-256 en el informe, ventanas solapadas cada 7 dias y liquidacion simulada al bid final. No son observaciones independientes y no demuestra ventaja predictiva ni rentabilidad futura. El resultado 1/92 para simple 3%/5% no determina un objetivo optimo.
- **Validacion:** 18 tests live quedaron excluidos deliberadamente; la suite offline paso. No se certifica comportamiento con exchange real.
