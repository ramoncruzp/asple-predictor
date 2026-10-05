# Discrepancias Fase 19G

- No se localizó Prompt_Codex_Fase19G_Boton_Cerrar_Dialogos.md en checkout ni adjuntos indexados. Para 19G-2/3 se siguieron los requisitos explícitos del mensaje actual.
- El botón Cerrar de la página de detalle puede coexistir con el Cerrar del modal; las pruebas seleccionan el botón dentro de #grid-action-dialog.
- frontend/scanner.js:175-184 conserva Cancelar para revisar plan y Cerrar tras éxito.
- Otras pantallas no revisadas visualmente: NO VERIFICADO.


## Cierre
- Se restauraron los avisos de cercanía al techo/piso con límites efectivos del rango recomendado; range_preference_note no se pisa.
- Se verificó la mutación del aviso de 2% con restauración SHA-256.
- sigma_widen_factor es null sin ampliación, y el mensaje UI usa el texto solicitado.


La prueba API parametrizada verifica cercanía al techo, cercanía al piso, ausencia de aviso y coexistencia sin pisar range_preference_note. La mutación quitando la condición del aviso del techo falla por aserción y se restaura con hash idéntico.
