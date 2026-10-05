# Discrepancias — Fase 18B-5

- La API responde en inglés con `current price must be strictly inside range`; no fue un cambio de contrato del backend.
- La traducción «El precio actual está fuera del rango; ajusta el rango para ver la vista previa» pertenecía al frontend y se perdió en 18B-4.
- D1 restaura esa traducción solo para esos mensajes de rango; los demás errores conservan su texto actual.
