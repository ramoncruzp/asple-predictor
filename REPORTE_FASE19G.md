# Reporte Fase 19G

## Cambios con referencias
- frontend/grids.js:146: eliminada la comilla extra; data-action-cancel queda como atributo correcto.
- frontend/grids.js:134,136: traduce estados incompleto y completado con statusLabel.
- frontend/grids.js:143-160: resultados Cerrar; preparación/revisión Cancelar y Continuar; Escape y reactivación conservados. HTTP con error se presenta como diálogo Error de acción cerrable; 403 conserva el flujo de token.
- api/routes/grid_advisor.py:217-220,307-317,341-350: range_centered incluye sigma_widened/sigma_widen_factor; range_preference_note solo se informa en modo centrado y separado de range_position_warning.
- frontend/app.js:351-353: muestra nota de preferencia y la ampliación de σ.
- tests/test_grid_advisor.py; tests/ui/test_grids_browser.py: pruebas de contrato API y UI añadidas/actualizadas.
- frontend/scanner.js:175-184: Cancelar sigue en la revisión; Cerrar tras éxito. Sin cambios.

## Antes/después
| Diálogo | Antes | Después |
|---|---|---|
| Acción completada | Cancelar; CLOSED | Cerrar; Cerrado |
| Acción incompleta | Cancelar; estado crudo | Cerrar; estado traducido |
| Error de acción | Cancelar | Cerrar después de HTTP 500 mock |
| Preparar / Revisar plan | Cancelar / Continuar | Cancelar / Continuar |

## node --check
[
  {
    "file": "frontend/grids.js",
    "exit": 0,
    "stderr": ""
  },
  {
    "file": "frontend/app.js",
    "exit": 0,
    "stderr": ""
  }
]

## Cinco mutaciones; hashes SHA-256
[
  {
    "name": "a: Cancelar siempre",
    "exit": 1,
    "fallo_de_asercion": true,
    "fallas": [
      "FAILED tests/ui/test_grids_browser.py::test_completed_action_result_is_translated_and_closable"
    ],
    "tail": "l.py:14: DeprecationWarning: websockets.server.WebSocketServerProtocol is deprecated\n    from websockets.server import WebSocketServerProtocol\n\n-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html\n- Hosts externos bloqueados y registrados por Playwright: cdn.jsdelivr.net, fonts.googleapis.com -\n------- Defectos de UI observados y no corregidos: (ninguno observado) --------\n=========================== short test summary info ===========================\nFAILED tests/ui/test_grids_browser.py::test_completed_action_result_is_translated_and_closable\n1 failed, 2 warnings in 6.90s\n",
    "sha256_before": "14ef719f455935a9b4a6e97552e3f183ab99a289ba4a2107fe49367a200c88f1",
    "sha256_after": "14ef719f455935a9b4a6e97552e3f183ab99a289ba4a2107fe49367a200c88f1",
    "byte_identical": true
  },
  {
    "name": "b: mostrar status crudo",
    "exit": 1,
    "fallo_de_asercion": true,
    "fallas": [
      "FAILED tests/ui/test_grids_browser.py::test_completed_action_result_is_translated_and_closable"
    ],
    "tail": "l.py:14: DeprecationWarning: websockets.server.WebSocketServerProtocol is deprecated\n    from websockets.server import WebSocketServerProtocol\n\n-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html\n- Hosts externos bloqueados y registrados por Playwright: cdn.jsdelivr.net, fonts.googleapis.com -\n------- Defectos de UI observados y no corregidos: (ninguno observado) --------\n=========================== short test summary info ===========================\nFAILED tests/ui/test_grids_browser.py::test_completed_action_result_is_translated_and_closable\n1 failed, 2 warnings in 6.73s\n",
    "sha256_before": "14ef719f455935a9b4a6e97552e3f183ab99a289ba4a2107fe49367a200c88f1",
    "sha256_after": "14ef719f455935a9b4a6e97552e3f183ab99a289ba4a2107fe49367a200c88f1",
    "byte_identical": true
  },
  {
    "name": "c: nota también en estructural",
    "exit": 1,
    "fallo_de_asercion": true,
    "fallas": [
      "FAILED tests/test_grid_advisor.py::test_advisor_defaults_centered_and_keeps_structural_range_available"
    ],
    "tail": "ural[\"recommended_floor\"] == structural[\"range_structural\"][\"floor\"]\n        assert structural[\"recommended_ceiling\"] == structural[\"range_structural\"][\"ceiling\"]\n>       assert structural[\"range_preference_note\"] is None\nE       AssertionError: assert 'El precio está a menos de 1 ATR de un borde estructural; se prefiere el rango centrado.' is None\n\ntests\\test_grid_advisor.py:233: AssertionError\n=========================== short test summary info ===========================\nFAILED tests/test_grid_advisor.py::test_advisor_defaults_centered_and_keeps_structural_range_available\n1 failed in 1.93s\n",
    "sha256_before": "b07441d899d1277f7580818b7563f87db0d119b77f0afb4128cf65012c73599b",
    "sha256_after": "b07441d899d1277f7580818b7563f87db0d119b77f0afb4128cf65012c73599b",
    "byte_identical": true
  },
  {
    "name": "d: factor σ incorrecto",
    "exit": 1,
    "fallo_de_asercion": true,
    "fallas": [
      "FAILED tests/test_grid_advisor.py::test_advisor_widens_only_low_dispersion_and_bias_is_informational"
    ],
    "tail": "1.25)\n        assert low[\"range_risk\"][\"sigma_24h\"] == pytest.approx(.05)\n        assert low[\"range_centered\"][\"sigma_widened\"] is True\n>       assert low[\"range_centered\"][\"sigma_widen_factor\"] == pytest.approx(1.25)\nE       assert 1.0 == 1.25 ± 1.2e-06\nE         \nE         comparison failed\nE         Obtained: 1.0\nE         Expected: 1.25 ± 1.2e-06\n\ntests\\test_grid_advisor.py:135: AssertionError\n=========================== short test summary info ===========================\nFAILED tests/test_grid_advisor.py::test_advisor_widens_only_low_dispersion_and_bias_is_informational\n1 failed in 1.95s\n",
    "sha256_before": "b07441d899d1277f7580818b7563f87db0d119b77f0afb4128cf65012c73599b",
    "sha256_after": "b07441d899d1277f7580818b7563f87db0d119b77f0afb4128cf65012c73599b",
    "byte_identical": true
  },
  {
    "name": "e: ocultar nota de ampliación UI",
    "exit": 1,
    "fallo_de_asercion": true,
    "fallas": [
      "FAILED tests/ui/test_grids_browser.py::test_grid_advisor_explains_margin_risk_and_prefills_scanner"
    ],
    "tail": ":14: DeprecationWarning: websockets.server.WebSocketServerProtocol is deprecated\n    from websockets.server import WebSocketServerProtocol\n\n-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html\n- Hosts externos bloqueados y registrados por Playwright: cdn.jsdelivr.net, fonts.googleapis.com -\n------- Defectos de UI observados y no corregidos: (ninguno observado) --------\n=========================== short test summary info ===========================\nFAILED tests/ui/test_grids_browser.py::test_grid_advisor_explains_margin_risk_and_prefills_scanner\n1 failed, 2 warnings in 6.84s\n",
    "sha256_before": "c4928dd4089d6857dda3b10e0d583f5c824c6aefdb7e83dcd6e91fe266b7068f",
    "sha256_after": "c4928dd4089d6857dda3b10e0d583f5c824c6aefdb7e83dcd6e91fe266b7068f",
    "byte_identical": true
  }
]

## Prueba final dirigida
python -m pytest tests/test_grid_advisor.py tests/ui/test_grids_browser.py::test_liquidate_requires_typed_confirmation_and_posts_preview_first tests/ui/test_grids_browser.py::test_incomplete_action_result_uses_close_button_and_escape tests/ui/test_grids_browser.py::test_cancel_and_escape_close_dialog_and_reenable_controls tests/ui/test_grids_browser.py::test_completed_action_result_is_translated_and_closable tests/ui/test_grids_browser.py::test_action_api_error_result_uses_close_button_and_escape tests/ui/test_grids_browser.py::test_grid_advisor_explains_margin_risk_and_prefills_scanner tests/test_frontend_encoding.py --basetemp "$env:TEMP\pytest-19g" -q
ASPLE_OFFLINE=1
Exit code: 0

.........................                                                [100%]
============================== warnings summary ===============================
tests/ui/test_grids_browser.py::test_liquidate_requires_typed_confirmation_and_posts_preview_first[ganancia]
  C:\APPS\ASPLE_Predictor\asple-predictor\venv\Lib\site-packages\websockets\legacy\__init__.py:6: DeprecationWarning: websockets.legacy is deprecated; see https://websockets.readthedocs.io/en/stable/howto/upgrade.html for upgrade instructions
    warnings.warn(  # deprecated in 14.0 - 2024-11-09

tests/ui/test_grids_browser.py::test_liquidate_requires_typed_confirmation_and_posts_preview_first[ganancia]
  C:\APPS\ASPLE_Predictor\asple-predictor\venv\Lib\site-packages\uvicorn\protocols\websockets\websockets_impl.py:14: DeprecationWarning: websockets.server.WebSocketServerProtocol is deprecated
    from websockets.server import WebSocketServerProtocol

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
- Hosts externos bloqueados y registrados por Playwright: cdn.jsdelivr.net, fonts.googleapis.com -
------- Defectos de UI observados y no corregidos: (ninguno observado) --------
25 passed, 2 warnings in 8.33s


NO VERIFICADO: aperturas reales y revisión visual de otras pantallas.
Resultados conductuales de las mutaciones: (a) la prueba completada requiere el botón Cerrar; (b) requiere Estado: Cerrado y no CLOSED; (c) el API espera range_preference_note vacío en modo estructural; (d) espera sigma_widen_factor=1.25; (e) la UI espera visible la nota de ampliación por estrés.
Bytes comprobados: UTF-8, sin BOM, CR ni U+FFFD; cero 0x3F entre letras en frontend, pruebas y reportes. El delimitador de query URL del fixture UI se construye con chr(63). Sin Binance/Testnet ni .env; no stage/commit/push.


## Cierre de regresión y sigma
- api/routes/grid_advisor.py:309-319: recuperados distance_ceiling/distance_floor y los avisos exactos de 2% sobre el rango recomendado; range_preference_note permanece independiente.
- tests/test_grid_advisor.py: prueba parametrizada cubre techo (<2%), piso (<2%), ausencia de aviso y coexistencia con range_preference_note.
- frontend/app.js:351: texto actualizado a “La volatilidad está ensanchada ×{k}: la probabilidad real de salir por cada lado es menor que la indicada.”
- api/routes/grid_advisor.py:220: sigma_widen_factor es null si sigma_widened es falso.
- node --check: [{"file": "frontend/grids.js", "exit": 0, "stdout": "", "stderr": ""}, {"file": "frontend/app.js", "exit": 0, "stdout": "", "stderr": ""}]
- Mutación regresión (elimina la condición del aviso de techo): {
  "exit": 1,
  "behavior_assertion_failed": true,
  "failed": [
    "FAILED tests/test_grid_advisor.py::test_recommended_range_position_warning_is_separate_from_preference_note[cerca-techo]"
  ],
  "tail": "cerca del piso: casi todo el capital quedaría en ventas\"),\n         (.97, 1.03, None)],\n        ids=[\"cerca-techo\", \"cerca-piso\", \"sin-aviso\"],\n    )\n    def test_recommended_range_position_warning_is_separate_from_preference_note(\n            monkeypatch, floor_factor, ceiling_factor, expected):\n        def centered(price, sigma_24h, risk):\n            floor, ceiling = price * floor_factor, price * ceiling_factor\n            return {\"floor\": floor, \"ceiling\": ceiling,\n                \"range_pct\": (ceiling - floor) / price * 100,\n                \"limited_by_profile\": False}\n    \n        monkeypatch.setattr(grid_advisor, \"_centered_range\", centered)\n        result = make_client().get(\"/api/grid/recommend\", query={\"symbol\": \"ADAUSDT\"}).body\n>       assert result[\"range_position_warning\"] == expected\nE       AssertionError: assert None == 'El precio está cerca del techo: casi todo el capital quedaría en compras'\n\ntests\\test_grid_advisor.py:253: AssertionError\n=========================== short test summary info ===========================\nFAILED tests/test_grid_advisor.py::test_recommended_range_position_warning_is_separate_from_preference_note[cerca-techo]\n1 failed, 2 passed in 2.09s\n",
  "sha256_before": "c9db2146798611725ca6a5e271f5f35eb08ee42073aba7aefb09c3a6fd962950",
  "sha256_after": "c9db2146798611725ca6a5e271f5f35eb08ee42073aba7aefb09c3a6fd962950",
  "restored_byte_identically": true
}
- Prueba final solicitada: python -m pytest tests/test_grid_advisor.py tests/ui/test_grids_browser.py::test_liquidate_requires_typed_confirmation_and_posts_preview_first tests/ui/test_grids_browser.py::test_incomplete_action_result_uses_close_button_and_escape tests/ui/test_grids_browser.py::test_cancel_and_escape_close_dialog_and_reenable_controls tests/ui/test_grids_browser.py::test_completed_action_result_is_translated_and_closable tests/ui/test_grids_browser.py::test_action_api_error_result_uses_close_button_and_escape tests/ui/test_grids_browser.py::test_grid_advisor_explains_margin_risk_and_prefills_scanner tests/test_frontend_encoding.py --basetemp "$env:TEMP\pytest-19g-close" -q
Exit code: 1

F...........................                                             [100%]
================================== FAILURES ===================================
_______ test_recommendation_uses_editable_margin_and_named_risk_limits ________

    def test_recommendation_uses_editable_margin_and_named_risk_limits():
        client = make_client()
        low = client.get("/api/grid/recommend", query={"symbol": "ADAUSDT", "capital": 1000,
            "risk": "low", "days": 90, "margin_target_pct": .7, "range_mode": "estructural"}).body
        high = client.get("/api/grid/recommend", query={"symbol": "ADAUSDT", "capital": 1000,
            "risk": "high", "days": 90, "margin_target_pct": .5, "range_mode": "estructural"}).body
        assert high["recommended_floor"] < low["recommended_floor"]
        assert low["risk"]["max_range_pct"] == 25
        assert high["risk"]["max_range_pct"] == 70
        assert low["margin_target_pct"] == .7
        assert "dust_estimate_pct" in low and "net_margin_pct" in low
        assert low["prediction_signal"] is None
        assert low["range_preference_note"] is None
>       assert low["range_position_warning"] is None
E       AssertionError: assert 'El precio está cerca del techo: casi todo el capital quedaría en compras' is None

tests\test_grid_advisor.py:61: AssertionError
============================== warnings summary ===============================
tests/ui/test_grids_browser.py::test_liquidate_requires_typed_confirmation_and_posts_preview_first[ganancia]
  C:\APPS\ASPLE_Predictor\asple-predictor\venv\Lib\site-packages\websockets\legacy\__init__.py:6: DeprecationWarning: websockets.legacy is deprecated; see https://websockets.readthedocs.io/en/stable/howto/upgrade.html for upgrade instructions
    warnings.warn(  # deprecated in 14.0 - 2024-11-09

tests/ui/test_grids_browser.py::test_liquidate_requires_typed_confirmation_and_posts_preview_first[ganancia]
  C:\APPS\ASPLE_Predictor\asple-predictor\venv\Lib\site-packages\uvicorn\protocols\websockets\websockets_impl.py:14: DeprecationWarning: websockets.server.WebSocketServerProtocol is deprecated
    from websockets.server import WebSocketServerProtocol

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
- Hosts externos bloqueados y registrados por Playwright: cdn.jsdelivr.net, fonts.googleapis.com -
------- Defectos de UI observados y no corregidos: (ninguno observado) --------
=========================== short test summary info ===========================
FAILED tests/test_grid_advisor.py::test_recommendation_uses_editable_margin_and_named_risk_limits
1 failed, 27 passed, 2 warnings in 8.42s


## Validación final del cierre
- node --check frontend/grids.js y frontend/app.js: ambos exit 0.
- Mutación del aviso 2%: {"exit": 1, "behavior_assertion_failed": true, "failed": ["FAILED tests/test_grid_advisor.py::test_recommended_range_position_warning_is_separate_from_preference_note[cerca-techo]"], "tail": "      def centered(price, sigma_24h, risk):\n            floor, ceiling = price * floor_factor, price * ceiling_factor\n            return {\"floor\": floor, \"ceiling\": ceiling,\n                \"range_pct\": (ceiling - floor) / price * 100,\n                \"limited_by_profile\": False}\n    \n        monkeypatch.setattr(grid_advisor, \"_centered_range\", centered)\n        result = make_client().get(\"/api/grid/recommend\", query={\"symbol\": \"ADAUSDT\"}).body\n>       assert result[\"range_position_warning\"] == expected\nE       AssertionError: assert None == 'El precio está cerca del techo: casi todo el capital quedaría en compras'\n\ntests\\test_grid_advisor.py:253: AssertionError\n=========================== short test summary info ===========================\nFAILED tests/test_grid_advisor.py::test_recommended_range_position_warning_is_separate_from_preference_note[cerca-techo]\n1 failed, 2 passed in 1.93s\n", "sha256_before": "c9db2146798611725ca6a5e271f5f35eb08ee42073aba7aefb09c3a6fd962950", "sha256_after": "c9db2146798611725ca6a5e271f5f35eb08ee42073aba7aefb09c3a6fd962950", "restored_byte_identically": true}
- Comando final: python -m pytest tests/test_grid_advisor.py tests/ui/test_grids_browser.py::test_liquidate_requires_typed_confirmation_and_posts_preview_first tests/ui/test_grids_browser.py::test_incomplete_action_result_uses_close_button_and_escape tests/ui/test_grids_browser.py::test_cancel_and_escape_close_dialog_and_reenable_controls tests/ui/test_grids_browser.py::test_completed_action_result_is_translated_and_closable tests/ui/test_grids_browser.py::test_action_api_error_result_uses_close_button_and_escape tests/ui/test_grids_browser.py::test_grid_advisor_explains_margin_risk_and_prefills_scanner tests/test_frontend_encoding.py --basetemp "$env:TEMP\pytest-19g-close" -q
ASPLE_OFFLINE=1
- Exit code: 0
- Resultado: ............................                                             [100%]
============================== warnings summary ===============================
tests/ui/test_grids_browser.py::test_liquidate_requires_typed_confirmation_and_posts_preview_first[ganancia]
  C:\APPS\ASPLE_Predictor\asple-predictor\venv\Lib\site-packages\websockets\legacy\__init__.py:6: DeprecationWarning: websockets.legacy is deprecated; see https://websockets.readthedocs.io/en/stable/howto/upgrade.html for upgrade instructions
    warnings.warn(  # deprecated in 14.0 - 2024-11-09

tests/ui/test_grids_browser.py::test_liquidate_requires_typed_confirmation_and_posts_preview_first[ganancia]
  C:\APPS\ASPLE_Predictor\asple-predictor\venv\Lib\site-packages\uvicorn\protocols\websockets\websockets_impl.py:14: DeprecationWarning: websockets.server.WebSocketServerProtocol is deprecated
    from websockets.server import WebSocketServerProtocol

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
- Hosts externos bloqueados y registrados por Playwright: cdn.jsdelivr.net, fonts.googleapis.com -
------- Defectos de UI observados y no corregidos: (ninguno observado) --------
28 passed, 2 warnings in 8.67s

- Byte audit posterior al reporte:
{
  "api\\routes\\grid_advisor.py": {
    "BOM": false,
    "CR": false,
    "U+FFFD": false,
    "0x3F_between_letters": false
  },
  "frontend\\app.js": {
    "BOM": false,
    "CR": false,
    "U+FFFD": false,
    "0x3F_between_letters": false
  },
  "frontend\\grids.js": {
    "BOM": false,
    "CR": false,
    "U+FFFD": false,
    "0x3F_between_letters": false
  },
  "tests\\test_grid_advisor.py": {
    "BOM": false,
    "CR": false,
    "U+FFFD": false,
    "0x3F_between_letters": false
  },
  "tests\\ui\\test_grids_browser.py": {
    "BOM": false,
    "CR": false,
    "U+FFFD": false,
    "0x3F_between_letters": false
  },
  "REPORTE_FASE19G.md": {
    "BOM": false,
    "CR": false,
    "U+FFFD": false,
    "0x3F_between_letters": false
  },
  "DISCREPANCIAS_FASE19G.md": {
    "BOM": false,
    "CR": false,
    "U+FFFD": false,
    "0x3F_between_letters": false
  }
}