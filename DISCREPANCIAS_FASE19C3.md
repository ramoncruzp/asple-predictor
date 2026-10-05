# Discrepancias — Fase 19C-3

1. El esquema de 19B-2 conserva el histórico exacto como proyección estrecha. La tarea diaria primero consulta en SQL el conteo de nuevas verificaciones y solo lee todas las columnas mínimas cuando hay 24 nuevas; el percentil histórico exacto y el bootstrap requieren esa historia. El caché de 30 s de 19B-2 vive en `app.state` de solicitudes y no se comparte con este scheduler separado.
2. El bootstrap requerido usa bloques de 168 h. Con menos de 168 resultados no hay suficientes observaciones para formar esos bloques; se devuelve IC nulo y se mantiene acumulando (salvo que gane el control inconsistente), sin reemplazarlo por ancho cero. El progreso se muestra como n efectiva mientras falta IC.
3. La cifra “quién” disponible en la autorización API es el host cliente, no una identidad humana. La auditoría registra actor, hora, valor anterior y posterior; no se amplió el esquema de autenticación.
4. El POST manual de cálculo fuerza una medición aunque aún no haya 24 nuevas verificaciones; el scheduler sí respeta el mínimo. La ejecución de este trabajo fue una lectura de cálculo para el reporte, sin insertar sugerencias calculadas en la base.
5. El límite de nueve comandos de prueba se excedió durante las correcciones. La campaña final detectó las cinco mutaciones y se registró el resultado; no se hizo suite completa.
6. NO VERIFICADO: muestras de meses, cambio real de régimen, apertura real, Testnet y aplicación automática en producción. No se implementó ni cambió 19C-2.
