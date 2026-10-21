# Reporte Fase 39 — ganancias en Cuenta

## Cambios

### A — Serie completa y mensual

- `database/db_manager.py`: `list_grid_pnl_events(grid_ids=None)` consulta sin límite solo `SELL_FILLED`, `CELL_STOPLOSS` y `CELL_LIQUIDATED`, ordenados cronológicamente y con `details` decodificado. La cuenta obtiene el flujo de todos sus grids con una sola consulta.
- `grid/status_view.py`: `monthly_profit_view(daily_rows)` agrega los días locales por `YYYY-MM`, sumando en `Decimal` neto, ciclos, stop-loss y liquidaciones, y contando días con eventos.
- `api/routes/grid_account.py`: agrega `gains.daily.all` y `gains.monthly`. Conserva `daily.7d` y `daily.30d` como subconjuntos de fechas calendario completas de `America/Santo_Domingo`, incluyendo hoy.
- Las pruebas cubren eventos sobre el borde UTC/local del mes, más de 5000 eventos que no son PnL, una sola consulta y ventanas calendario.

### B — Un gráfico con selector

- `frontend/cuenta.js` reemplaza las dos gráficas anteriores por “Ganancia realizada”, con los selectores **7 días**, **Mes** y **Meses**. Los días y meses vacíos se rellenan con cero; el modo Mes muestra el mes completo o hasta hoy para el actual y permite navegar entre el primer mes con datos y el actual.
- Las barras incluyen monto y tooltip con fecha, neto y desglose. Ganancias positivas se dibujan verdes hacia arriba y pérdidas rojas hacia abajo desde cero. El encabezado muestra total, conteos de ganancia/pérdida/inactividad, mejor y peor periodo y promedio con actividad.
- La selección de periodo y mes se conserva en `localStorage` con manejo de errores; también sobrevive al refresco automático y a una recarga. En móvil, el desplazamiento del modo Mes queda dentro del gráfico.
- Descripción visual: **7 días** presenta siete barras con día de semana y fecha; **Mes** presenta una barra por día con navegación del mes; **Meses** presenta una barra por cada mes desde el primero con datos hasta el actual.

## Pruebas

Se ejecutó únicamente el conjunto indicado, con `ASPLE_OFFLINE=1` y un `--basetemp` nuevo en `%TEMP%`:

```text
pytest tests/test_grid_status_view.py tests/test_grid_account_api.py tests/ui/test_cuenta_ui.py tests/ui/test_frontend_syntax.py -q
54 passed, 2 warnings in 3.28s
```

La prueba UI cambió a Mes, comprobó una barra por día, montos visibles y total, navegó al mes anterior completo en viewport móvil para comprobar el scroll interno y confirmó la persistencia al recargar. El guard de sintaxis JavaScript y su mutación pasaron. Playwright no registró hosts externos ni defectos UI.

## Commits

- `e04e1e8 Fase 39: serie de ganancias completa y mensual`
- `d9fefb3 Fase 39: grafico unico de ganancias con selector`
- El reporte se registra en un commit separado al final.
