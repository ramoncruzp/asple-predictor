# Discrepancias ? Fase 18B-2

1. La auditor?a citada como `Auditoria_Fase18B.md` no est? disponible en el workspace revisado. La implementaci?n se contrast? contra los puntos K1?K7 del prompt pegado.
2. K5: el caso de di?logo fuera de rango se cubre mediante fixture; el endpoint real no entrega plan cuando `plan_cells` rechaza un mid fuera del rango. Se conserv? esa validaci?n del plan y se documenta la limitaci?n en el informe.
3. No se verificaron m?nimos/filtros reales de Binance/Testnet ni comportamiento en una cuenta real; se respet? la prohibici?n de red y `.env`.
