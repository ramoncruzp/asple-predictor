# Reporte Fase 19F

## Resultado
- Scanner UI: Smart es la selección inicial; conserva un valor explícito del borrador. Default API intacto.
- Advisor: ofrece rango centrado por σ y estructural, recomienda el centrado y expone su disponibilidad/fallback.
- Replay: inicia en la primera vela cuyo cierre está estrictamente dentro del rango; la simulación conserva desde allí las velas siguientes y presenta fecha/duración/aviso.

## Comparación antes/después
| Aspecto | Antes | Después |
|---|---|---|
| Estrategia inicial de Scanner | Simple | Smart |
| Rango por defecto en Advisor | Estructural | Centrado por σ (fallback estructural sin σ) |
| Inicio de replay | Ventana completa | Primera vela con cierre estrictamente interior |
| Tabla cuantitativa histórica | No disponible en materiales visibles | No se inventan cifras |

## Mutaciones
Cada alteración hizo fallar su prueba dirigida. Hashes SHA-256 antes/después y resultado:

[
  {
    "mutation": "Smart UI predeterminado",
    "exit": 1,
    "fallo_detectado": true,
    "prueba_fallida": [
      "FAILED tests/ui/test_grids_browser.py::test_grid_advisor_explains_margin_risk_and_prefills_scanner"
    ],
    "sha256_before": "682954d53ef995ef347ac0fecd9141d831602fd220025f4b6c540464967f2fd6",
    "sha256_after": "682954d53ef995ef347ac0fecd9141d831602fd220025f4b6c540464967f2fd6",
    "restaurado": true
  },
  {
    "mutation": "perfil sigma incorrecto",
    "exit": 1,
    "fallo_detectado": true,
    "prueba_fallida": [
      "FAILED tests/test_grid_advisor.py::test_centered_range_profile_touch_targets_symmetry_and_cap"
    ],
    "sha256_before": "bf106f02c0a6e6b35cdb8743648ae8440d0d73a72b47963a59ad6a9646b64270",
    "sha256_after": "bf106f02c0a6e6b35cdb8743648ae8440d0d73a72b47963a59ad6a9646b64270",
    "restaurado": true
  },
  {
    "mutation": "rango centrado asimétrico",
    "exit": 1,
    "fallo_detectado": true,
    "prueba_fallida": [
      "FAILED tests/test_grid_advisor.py::test_centered_range_profile_touch_targets_symmetry_and_cap"
    ],
    "sha256_before": "bf106f02c0a6e6b35cdb8743648ae8440d0d73a72b47963a59ad6a9646b64270",
    "sha256_after": "bf106f02c0a6e6b35cdb8743648ae8440d0d73a72b47963a59ad6a9646b64270",
    "restaurado": true
  },
  {
    "mutation": "replay inicia antes de la vela válida",
    "exit": 1,
    "fallo_detectado": true,
    "prueba_fallida": [
      "FAILED tests/test_grid_advisor.py::test_historical_simulation_starts_at_first_strictly_in_range_candle"
    ],
    "sha256_before": "bf106f02c0a6e6b35cdb8743648ae8440d0d73a72b47963a59ad6a9646b64270",
    "sha256_after": "bf106f02c0a6e6b35cdb8743648ae8440d0d73a72b47963a59ad6a9646b64270",
    "restaurado": true
  },
  {
    "mutation": "borrador sin estrategia inicia simple",
    "exit": 1,
    "fallo_detectado": true,
    "prueba_fallida": [
      "FAILED tests/ui/test_grids_browser.py::test_grid_advisor_explains_margin_risk_and_prefills_scanner"
    ],
    "sha256_before": "682954d53ef995ef347ac0fecd9141d831602fd220025f4b6c540464967f2fd6",
    "sha256_after": "682954d53ef995ef347ac0fecd9141d831602fd220025f4b6c540464967f2fd6",
    "restaurado": true
  }
]

## Prueba dirigida final
ASPLE_OFFLINE=1; pytest tests/test_grid_advisor.py tests/ui/test_grids_browser.py::test_grid_advisor_explains_margin_risk_and_prefills_scanner tests/test_frontend_encoding.py --basetemp TEMP/pytest-19f -q

Exit code: 0

..................                                                       [100%]
============================== warnings summary ===============================
tests/ui/test_grids_browser.py::test_grid_advisor_explains_margin_risk_and_prefills_scanner
  C:\APPS\ASPLE_Predictor\asple-predictor\venv\Lib\site-packages\websockets\legacy\__init__.py:6: DeprecationWarning: websockets.legacy is deprecated; see https://websockets.readthedocs.io/en/stable/howto/upgrade.html for upgrade instructions
    warnings.warn(  # deprecated in 14.0 - 2024-11-09

tests/ui/test_grids_browser.py::test_grid_advisor_explains_margin_risk_and_prefills_scanner
  C:\APPS\ASPLE_Predictor\asple-predictor\venv\Lib\site-packages\uvicorn\protocols\websockets\websockets_impl.py:14: DeprecationWarning: websockets.server.WebSocketServerProtocol is deprecated
    from websockets.server import WebSocketServerProtocol

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
- Hosts externos bloqueados y registrados por Playwright: cdn.jsdelivr.net, fonts.googleapis.com -
------- Defectos de UI observados y no corregidos: (ninguno observado) --------
18 passed, 2 warnings in 4.62s

## Caso ADA descrito en la auditoría adjunta
La evidencia anterior reportada por el usuario: precio/resistencia 0,2657; piso 0,2105; techo 0,2677; toque del techo 85,7% a 24 h y 94,6% a 168 h; capital en compras 100%. La auditoría no entrega toques 72 h, número de niveles, capital USDT/celda ni margen numérico.

Para ilustrar el cálculo nuevo se usa σ24 de ejemplo = 3%, perfil Moderado y la regla declarada (z=1.036433); estos son cálculos de fixture, no una recalculación del snapshot local/mercado:
| Métrica | Antes (auditoría aportada) | Después (ejemplo matemático, no dato observado) |
|---|---:|---:|
| Piso | 0,2105 | 0.251769 |
| Techo | 0,2677 | 0.280402 |
| Probabilidad de toque por lado, 24 h | 85,7% | 7.3% |
| Probabilidad de toque por lado, 72 h | No informada | 30.0% |
| Probabilidad de toque por lado, 168 h | 94,6% | 49.7% |
| Niveles | No informados | NO VERIFICADO |
| Capital por celda | No informado | NO VERIFICADO |
| Margen | No informado | NO VERIFICADO |
| Capital asignado a compras | 100% | NO VERIFICADO |

La fixture de integración de tests no reproduce ADA a 0,2657 ni una lectura de base local para esta auditoría. No se presentan niveles/margen como si fueran resultados del caso real.

## Referencias de implementación y pruebas
- api/routes/grid_advisor.py:25-41 (probabilidad, z y simetría); :49-64 (ventana histórica); :171 (parámetro de modo); :215-224 (selección del rango recomendado); :330-354 (respuesta y replay).
- frontend/scanner.js:94,202 (defaults UI y estrategia ausente en borrador).
- frontend/app.js:347-353 (presentación de rangos, aviso fijo y datos de replay); :386 (envío del modo).
- Pruebas dirigidas: tests/test_grid_advisor.py::test_centered_range_profile_touch_targets_symmetry_and_cap (perfil/simetría/tope); ::test_advisor_defaults_centered_and_keeps_structural_range_available (API/default/fallback); ::test_historical_simulation_starts_at_first_strictly_in_range_candle (inicio/no disponible/duración); tests/ui/test_grids_browser.py::test_grid_advisor_explains_margin_risk_and_prefills_scanner (selector, Smart, borrador, aviso); tests/test_frontend_encoding.py (bytes UI).
- NO VERIFICADO: apertura real con rango centrado; beneficio real; Testnet con Smart por defecto.
- Bytes auditados: UTF-8, sin BOM, CR ni U+FFFD; app.js/scanner.js/reportes sin 0x3F entre letras y con tildes en reportes. No stage/commit/push.
