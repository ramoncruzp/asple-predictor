import csv
import hashlib
import io
import zipfile
from datetime import datetime, timezone

import pytest

from data.market_store import CandleStore
from scripts.velas_backfill import (BackfillRunner, ChecksumError, VisionClient,
    LISTA_INICIAL_DATOS, archive_specs, normalize_open_timestamp, parse_vision_zip, parse_args)


def _zip(rows):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        text = io.StringIO()
        writer = csv.writer(text)
        writer.writerows(rows)
        archive.writestr("candles.csv", text.getvalue())
    return output.getvalue()


def _row(ts):
    return [ts, "100", "102", "99", "101", "2", ts + 299999,
        "202", "3", "0"]


def test_parse_vision_normaliza_milisegundos_y_microsegundos():
    ms = 1_700_000_100_000
    rows, rejected = parse_vision_zip(_zip([_row(ms), _row(ms * 1000), ["bad"]]))
    assert [row["open_ts"] for row in rows] == [1_700_000_100, 1_700_000_100]
    assert rejected == 1
    assert normalize_open_timestamp(ms) == normalize_open_timestamp(ms * 1000)


class FakeVision:
    def __init__(self, archives):
        self.archives = archives
        self.calls = []

    def get_verified_zip(self, url):
        self.calls.append(url)
        return self.archives.get(url)


def test_dry_run_no_crea_ni_modifica_market_db(tmp_path):
    now = datetime(2026, 4, 3, tzinfo=timezone.utc)
    specs = list(archive_specs("XRPUSDT", "5m", 1, now=now))
    # El archivo mensual ausente se tolera y la jornada cerrada se valida sin escribir.
    daily = specs[-1]
    rows = [_row((daily.start_ts + index * 300) * 1000) for index in range(288)]
    client = FakeVision({daily.url: _zip(rows)})
    db = tmp_path / "market_data.db"
    result = BackfillRunner(None, client, sleep=lambda _: None, now=lambda: now).run_symbol(
        "XRPUSDT", months=1, dry_run=True)
    assert result["candles"] == 288
    assert result["missing_archives"] == 2
    assert not db.exists()


class FakeResponse:
    def __init__(self, status, content=b""):
        self.status_code = status
        self.content = content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(str(self.status_code))


class FakeSession:
    trust_env = True

    def __init__(self, mapping):
        self.mapping = mapping

    def get(self, url, timeout):
        return self.mapping.get(url, FakeResponse(404))


def test_checksum_sha256_incorrecto_se_rechaza():
    payload = _zip([_row(1_700_000_100_000)])
    session = FakeSession({
        "https://data.binance.vision/a.zip": FakeResponse(200, payload),
        "https://data.binance.vision/a.zip.CHECKSUM": FakeResponse(200, b"0" * 64),
    })
    client = VisionClient(session)
    assert session.trust_env is False
    with pytest.raises(ChecksumError):
        client.get_verified_zip("https://data.binance.vision/a.zip")


def test_backfill_persiste_y_reanuda_archivo_completo(tmp_path):
    now = datetime(2026, 4, 3, tzinfo=timezone.utc)
    specs = list(archive_specs("XRPUSDT", "5m", 1, now=now))
    spec = specs[-1]
    payload = _zip([_row((spec.start_ts + index * 300) * 1000) for index in range(288)])
    store = CandleStore(tmp_path / "market_data.db")
    client = FakeVision({spec.url: payload})
    runner = BackfillRunner(store, client, sleep=lambda _: None, now=lambda: now)
    first = runner.run_symbol("XRPUSDT", months=1)
    second = runner.run_symbol("XRPUSDT", months=1)
    assert first["candles"] == 288
    assert len(store.load("XRPUSDT", "5m", spec.start_ts, spec.end_ts - 1).timestamp) == 288
    assert second["resumed"] == 1
    assert client.calls.count(spec.url) == 1


def test_lista_inicial_es_constante_de_datos_y_seleccion_exclusiva():
    assert len(LISTA_INICIAL_DATOS) == 15
    assert "BTCUSDT" in LISTA_INICIAL_DATOS and "SHIBUSDT" in LISTA_INICIAL_DATOS
    assert parse_args(["--db", "market_data.db", "--lista-inicial"]).lista_inicial
