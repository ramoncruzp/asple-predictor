import shutil
import subprocess
from pathlib import Path

import pytest


APP_JS = Path(__file__).resolve().parents[1] / "frontend" / "app.js"
NODE = shutil.which("node")


def _run_node(assertions):
    if NODE is None:
        pytest.skip("node no está instalado; la prueba requiere Node y no navegador")
    harness = r"""
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const source = fs.readFileSync(process.argv[1], 'utf8');
const sandbox = {
  window: { location: { protocol: 'http:', origin: 'http://localhost' }, addEventListener() {} },
  document: { addEventListener() {} },
  console, URL, AbortController, fetch: async () => { throw new Error('not used'); },
  setTimeout, clearTimeout, localStorage: { getItem: () => null, setItem() {} }
};
vm.runInNewContext(source + '\nglobalThis.__test = { buildGridSymbolOptions, GRID_SYMBOL_FALLBACK, advanceApiHealth, apiHealthLabel, formatPrice, timeSinceSuccess };', sandbox);
"""
    result = subprocess.run([NODE, "-e", harness + "\n" + assertions, str(APP_JS)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def test_grid_symbol_registry_options_selection_fallback_and_escaping():
    _run_node(r"""
const { buildGridSymbolOptions, GRID_SYMBOL_FALLBACK } = sandbox.__test;
const registry = buildGridSymbolOptions([{symbol:'PEPEUSDT'}, {symbol:'XRPUSDT'}, {symbol:'PEPEUSDT'}], 'PEPEUSDT');
assert.deepStrictEqual(Array.from(registry.symbols), ['PEPEUSDT', 'XRPUSDT']);
assert.strictEqual(registry.selected, 'PEPEUSDT');
assert(registry.options.includes('value="PEPEUSDT" selected>PEPE/USDT</option>'));
assert.strictEqual(buildGridSymbolOptions([{symbol:'XRPUSDT'}], 'PEPEUSDT').selected, 'XRPUSDT');
assert.strictEqual(buildGridSymbolOptions(GRID_SYMBOL_FALLBACK, 'SOLUSDT').selected, 'SOLUSDT');
const escaped = buildGridSymbolOptions([{symbol:'<img>USDT'}], '');
assert(escaped.options.includes('&lt;IMG&gt;USDT'));
""")


def test_api_health_chip_hysteresis_and_success_recovery():
    _run_node(r"""
const { advanceApiHealth, apiHealthLabel, timeSinceSuccess } = sandbox.__test;
assert.strictEqual(apiHealthLabel({ status: 'checking' }), 'Comprobando…');
assert.strictEqual(apiHealthLabel({ status: 'online' }), 'API Online');
let state = { failures: 0, status: 'online', lastSuccessAt: 1000 };
state = advanceApiHealth(state, false, 31000); assert.strictEqual(state.status, 'slow'); assert.strictEqual(state.failures, 1);
state = advanceApiHealth(state, false, 61000); assert.strictEqual(state.status, 'slow'); assert.strictEqual(state.failures, 2);
state = advanceApiHealth(state, false, 91000); assert.strictEqual(state.status, 'offline'); assert.strictEqual(state.failures, 3);
assert.strictEqual(state.lastSuccessAt, 1000);
state = advanceApiHealth(state, true, 92000); assert.strictEqual(state.status, 'online'); assert.strictEqual(state.failures, 0); assert.strictEqual(state.lastSuccessAt, 92000);
assert.strictEqual(apiHealthLabel(state), 'API Online');
assert.strictEqual(timeSinceSuccess(1000, 92000), 'hace 1 min');
const healthSource = require('fs').readFileSync(process.argv[1], 'utf8');
assert(healthSource.includes("setOffline(state.status === 'offline')"));
assert(healthSource.includes("state.status === 'checking' ? 'Comprobando…'"));
""")


def test_price_formatter_preserves_small_values_without_scientific_notation():
    _run_node(r"""
const { formatPrice } = sandbox.__test;
assert.strictEqual(formatPrice(1.4744), '1.4744');
assert.strictEqual(formatPrice(1), '1.00');
assert.strictEqual(formatPrice(0.00000442), '0.000004420');
assert.strictEqual(formatPrice(84747.58), '84,747.58');
assert.strictEqual(formatPrice(0.2384), '0.2384');
assert.strictEqual(formatPrice(1.4842), '1.4842');
assert.strictEqual(formatPrice(0), '0');
assert.strictEqual(formatPrice(null), '\u2014');
assert.strictEqual(formatPrice('not-a-price'), '\u2014');
assert(!/[eE]/.test(formatPrice(0.0000000000000000000000001)));
""")


def test_panel_loaders_never_change_global_api_status():
    source = APP_JS.read_text(encoding="utf-8")
    assert source.count("setOffline(") == 2  # helper declaration plus health-render call only
    assert "setOffline(true)" not in source
    assert "setOffline(false)" not in source
