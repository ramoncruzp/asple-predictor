# Discrepancias Fase 43

- En el trabajo previo se concluyó que los mensajes señalados estaban bien; esta revisión encontró dos literales con mojibake en la validación de asignación ampliada del operador. Se corrigieron las tildes, sin cambiar la condición ni la conducta.
- `--lista-inicial` resta las monedas activas del Coin Registry si `asple_predictor.db` existe; sin esa BD local emplea la lista fija de datos. No se abrió la BD real durante el desarrollo ni las pruebas.
- El presupuesto del sincronizador limita la espera del hilo de trabajo a 60 s; el cliente existente conserva su política de reintentos. Si una operación en curso excede el límite, su hilo daemon puede terminar después del resumen, y el cursor persistido permite retomar desde la última vela escrita.
- La verificación API/BD es una herramienta optativa que requiere acceso público a Binance cuando se ejecuta con `--comparar-api`; en esta fase solo se ejercitó con cliente falso.
- No se ejecutó en S1 ni se usaron Testnet, `.env`, red o BD real. El archivo `market_data.db` tampoco se generó ni se añadió al repositorio.
- D6 cambia por la decisión de Ramón del 2026-10-30, pero el interruptor queda por defecto `true` para mantener el comportamiento anterior hasta el cambio manual autorizado. No se modificó ninguna BD de usuario.
- El apagado impide comenzar nuevas corridas y evita escrituras/etiquetas en las rutas protegidas. No cancela una corrida que ya estuviera ejecutándose al momento del cambio.
- C43-0: el plan `--fijar-papeles` solo actualiza símbolos ya existentes en `market_data.db`; no inventa monedas ni requiere abrir la BD de registro en las pruebas. El modo `--dry-run` consulta el almacén en solo lectura. La etiqueta de preparación para reactivar pesos sigue disponible dentro del diálogo oculto; el medidor visible es el que reemplaza su estado por el aviso de aprendizaje apagado.
- 43c-1: no se conecta a la BD de registro vivo; la ruta viva era opcional y no puede validarse sin BD real, prohibida en esta ejecución. El marcador local trabaja con `CandleData` sintético o con `CandleStore`.
- 43c-2/3: el HAR se implementa como regresión expandible con componentes de RV diaria/semanal/mensual y reestimación cada 168 horas. La conexión opcional a pronósticos vivos se omite, dado que requeriría la BD principal prohibida por las reglas.
- 43c-4: el cálculo actual persiste métricas de volatilidad (simple/EWMA/HAR) y factores de calibración; la entrada viva opcional se omite. No se midió el rendimiento de una moneda con dos años en esta entrega y no se ejecutó con `market_data.db` de despliegue.
- 43c-5: se entrega el panel y endpoint de lectura. El job semanal y su interruptor global no se habilitan en este alcance parcial; no se registra ningún job automático ni se cambia la configuración global.

- 43c parcial: a?n no se implementan los objetivos completos de ruptura, direcci?n, correlaci?n mensual ni evaluaci?n de r?gimen con intervalos por bloques. Los veredictos guardados se limitan a volatilidad.
- 43c-5: el panel de lectura s? est? disponible, pero faltan el job semanal apagado por defecto y su interruptor global. No hay tarea semanal registrada.
- HAR es una aproximaci?n funcional de regresi?n expansible; falta su validaci?n con series sint?ticas de volatilidad constante y cambiante. La cobertura del 80 % actual no est? validada fuera de muestra.
- No se ejecutaron estudios reales ni benchmarks en S1; no se conoce el tiempo por moneda con dos a?os de velas.

- 43c-9: con trayectoria sint?tica Student-t(3), varianza igual a GBM, el factor de colas fue 0,80, no >1 como se esperaba. Se conserv? el resultado emp?rico y no se ajust? el modelo para forzar el valor. La validaci?n de colas pesadas no est? satisfecha.
- 43c-10: el runner conecta y persiste m?tricas de ruptura, r?gimen y direcci?n; la correlaci?n mensual se alinea por timestamp y queda en summary_json. No se prob? todav?a matriz completa 21?21 en una corrida de 3 monedas ? 200 d?as.
- 43c-10: falta la prueba OU frente a tendencia exigida para comparar ER/ciclos. Las pruebas actuales cubren el ciclo unitario y el contrato de correlaci?n.
