from types import SimpleNamespace

from data.proxy_config import proxy_label, resolve_binance_proxy


def test_resuelve_proxy_del_entorno_sin_consultar_systemd():
    def unexpected(*_args, **_kwargs):
        raise AssertionError("no debe consultar systemd si el entorno ya define proxy")

    proxy, source = resolve_binance_proxy(environ={"HTTPS_PROXY": "http://127.0.0.1:3128"},
        runner=unexpected)

    assert (proxy, source) == ("http://127.0.0.1:3128", "entorno")


def test_resuelve_solo_proxy_permitido_de_la_unidad_y_no_expone_otras_variables():
    calls = []

    def runner(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout="HTTPS_PROXY=http://127.0.0.1:3128")

    proxy, source = resolve_binance_proxy(environ={}, runner=runner)

    assert proxy == "http://127.0.0.1:3128"
    assert source == "servicio"
    assert calls[0][0][0:2] == ["bash", "-lc"]
    assert "systemctl show asple-predictor -p Environment --value" in calls[0][0][2]
    assert "grep -oE" in calls[0][0][2]
    label = proxy_label(proxy, source)
    assert label == "Proxy: 127.0.0.1:3128 (servicio)"
    assert "secreto" not in label and "API_KEY" not in label


def test_sin_proxy_retorna_aviso_y_no_interpreta_variables_ajenas():
    proxy, source = resolve_binance_proxy(environ={"API_KEY": "secreto"},
        runner=lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout=""))

    assert (proxy, source) == (None, None)
    assert proxy_label(proxy, source) == "Proxy: ninguno — puede fallar desde S1"
