# Discrepancias — Fase 17A-4

- **Prueba de navegador:** no se ejecutó un navegador. La selección dinámica, el estado ámbar y el texto del banner se cubrieron con funciones puras y verificaciones de fuente mediante Node/VM; la presentación visual en el DOM queda pendiente.
- **Dependencia de TestClient:** el `venv` no contiene `httpx`, requerido por `fastapi.testclient.TestClient`. No se instaló. Las pruebas de API usan un harness ASGI pequeño con la biblioteca estándar y no ejecutan el `lifespan`.
- **Expresión de grep indicada:** el comando literal `[A-Za-z]?[a-z]` devolvió 4 y 1 líneas para REPORTE y DISCREPANCIAS, respectivamente; en el grep instalado `?` cuantifica la clase anterior y el patrón no detecta sustituciones de tildes. La forma explícita `[A-Za-z][?][a-z]` devolvió cero en ambos informes. No quedan sustituciones ASCII de tildes/eñes.
- **Causa de lentitud:** no se investigó ni se atribuye una causa. El middleware entrega mediciones para solicitudes que superen el umbral configurado.

## Cierre de 17A-4

Health y el middleware quedaron cubiertos por pruebas ASGI sin acceso a servicios reales. Coin Registry y `formatPrice` tienen pruebas Node; la interacción de navegador no fue certificada. Sin commit ni push.
