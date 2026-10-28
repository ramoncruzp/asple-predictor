# Fase 42 — discrepancias y decisiones conservadoras

## C34

- El horizonte de 72 h se guarda al cumplir 72 h; el del perfil se guarda cuando cumple 20 o 45 días. Se emiten eventos incrementales enlazados a la recomendación, porque a los siete días aún no existe el resultado real del plazo. La vista solo cuenta una propuesta cuando ambos horizontes están etiquetados.
- Propuestas con geometría inválida se registran y no se incluyen en evidencia ni se vuelven a simular diariamente.
- No se ejecutó la suite completa, Testnet, S1 ni el job de mercado real.

## C35

- La caché horaria es local al proceso; no se persiste un snapshot adicional. Si el proceso reinicia, la siguiente ejecución vuelve a medir y el estado persistido del AlertDispatcher deduplica transiciones ya observadas.
- El job usa el evaluador interno de Inteligencia, sin autenticación HTTP ni abrir una petición local. La ruta pública conserva su protección.
