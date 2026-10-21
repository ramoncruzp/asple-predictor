# Instrucciones para Claude Code

## Idioma
- Responde siempre en español.
- Escribe en español los reportes (REPORTE_*, DISCREPANCIAS_*), las explicaciones de cada corrección y los mensajes de commit.
- Escribe en español los comentarios nuevos en el código. Los nombres de variables y funciones se quedan como están.

## Pruebas (pytest)
- Usa siempre una sola carpeta temporal: `--basetemp=.pytest_tmp`.
- No crees carpetas `.pytest-<algo>` nuevas por cada corrida de pruebas.
- Si necesitas guardar capturas de pantalla como evidencia, ponlas en `reports/capturas/<fase>/`, no en la carpeta temporal (pytest la borra en cada corrida).
