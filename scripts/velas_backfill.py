"""Rellena un almacén market_data.db con archivos públicos de Binance Vision."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import sqlite3
import sys
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data.market_store import CandleStore

VISION_HOST = "https://data.binance.vision"
SOURCE = "data.binance.vision"
# Lista de monedas de datos: decisión de Ramón (B3).
LISTA_INICIAL_DATOS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "DOGEUSDT",
    "AVAXUSDT", "LINKUSDT", "DOTUSDT", "LTCUSDT", "TRXUSDT", "SUIUSDT",
    "ARBUSDT", "NEARUSDT", "APTUSDT", "SHIBUSDT")


def initial_symbol_roles(operating_symbols):
    operating = list(dict.fromkeys(str(symbol).upper().replace("/", "")
        for symbol in operating_symbols))
    operating_set = set(operating)
    return ([(symbol, "operar") for symbol in operating] +
        [(symbol, "datos") for symbol in LISTA_INICIAL_DATOS if symbol not in operating_set])


def role_fix_plan(operating_symbols, current_roles):
    desired = {str(symbol).upper().replace("/", ""): "operar" for symbol in operating_symbols}
    for symbol in LISTA_INICIAL_DATOS:
        desired.setdefault(symbol, "datos")
    return [(symbol, current_roles[symbol], role) for symbol, role in sorted(desired.items())
        if symbol in current_roles and current_roles[symbol] != role]


def apply_role_fix_plan(store, changes, *, dry_run=False):
    if dry_run:
        return 0
    return sum(bool(store.set_symbol_role(symbol, role)) for symbol, _previous, role in changes)


class ChecksumError(ValueError):
    pass


class VisionClient:
    """Cliente exclusivo de data.binance.vision, sin proxy del entorno."""
    def __init__(self, session=None):
        self.session = session or requests.Session()
        self.session.trust_env = False

    def get(self, url):
        if not url.startswith(VISION_HOST + "/"):
            raise ValueError("VisionClient solo acepta URLs de data.binance.vision")
        response = self.session.get(url, timeout=60)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.content

    def get_verified_zip(self, url):
        content = self.get(url)
        if content is None:
            return None
        checksum = self.get(url + ".CHECKSUM")
        if checksum:
            expected = checksum.decode("ascii", errors="strict").strip().split()[0].lower()
            actual = hashlib.sha256(content).hexdigest()
            if len(expected) != 64 or actual != expected:
                raise ChecksumError(f"checksum SHA256 no coincide para {url.rsplit('/', 1)[-1]}")
        return content


def normalize_open_timestamp(value):
    """Convierte milisegundos o microsegundos de Vision a epoch en segundos."""
    stamp = int(value)
    if stamp > 100_000_000_000_000:
        stamp //= 1000
    if stamp > 100_000_000_000:
        stamp //= 1000
    return stamp


def parse_vision_zip(content):
    rows, rejected = [], 0
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError(f"ZIP inválido: {exc}") from exc
    with archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        if not names:
            return rows, 0
        with archive.open(names[0]) as binary:
            reader = csv.reader(io.TextIOWrapper(binary, encoding="utf-8"))
            for raw in reader:
                if not raw:
                    continue
                try:
                    rows.append({"open_ts": normalize_open_timestamp(raw[0]),
                        "open": raw[1], "high": raw[2], "low": raw[3], "close": raw[4],
                        "volume": raw[5], "quote_volume": raw[7], "trades": raw[8]})
                except (IndexError, TypeError, ValueError, OverflowError):
                    rejected += 1
    return rows, rejected


def active_registry_symbols(database_path):
    """Lee Coin Registry mediante SQLite mode=ro y devuelve solo monedas activas."""
    path = Path(database_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"No existe la BD del Coin Registry: {path}")
    uri = path.as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        rows = conn.execute("SELECT symbol FROM coins_registry WHERE active=1 ORDER BY added_at,symbol").fetchall()
        return [str(row[0]).upper().replace("/", "") for row in rows]
    finally:
        conn.close()


def _month_start(year, month):
    return datetime(year, month, 1, tzinfo=timezone.utc)


def _next_month(start):
    return datetime(start.year + (start.month == 12), 1 if start.month == 12 else start.month + 1,
        1, tzinfo=timezone.utc)


def archive_specs(symbol, interval="5m", months=24, *, now=None):
    current = now or datetime.now(timezone.utc)
    current = current.astimezone(timezone.utc)
    first_closed_month = _month_start(current.year, current.month)
    for _ in range(int(months)):
        previous = first_closed_month - timedelta(days=1)
        start = _month_start(previous.year, previous.month)
        end = _next_month(start)
        day = start.strftime("%Y-%m")
        filename = f"{symbol}-{interval}-{day}.zip"
        url = f"{VISION_HOST}/data/spot/monthly/klines/{symbol}/{interval}/{filename}"
        yield ArchiveSpec(url, int(start.timestamp()), int(end.timestamp()), "mensual")
        first_closed_month = start
    today = current.date()
    yesterday = today - timedelta(days=1)
    first_day = _month_start(current.year, current.month).date()
    day = first_day
    while day <= yesterday:
        start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        label = day.isoformat()
        filename = f"{symbol}-{interval}-{label}.zip"
        url = f"{VISION_HOST}/data/spot/daily/klines/{symbol}/{interval}/{filename}"
        yield ArchiveSpec(url, int(start.timestamp()), int(end.timestamp()), "diario")
        day += timedelta(days=1)


@dataclass(frozen=True)
class ArchiveSpec:
    url: str
    start_ts: int
    end_ts: int
    kind: str


class BackfillRunner:
    def __init__(self, store, client=None, *, sleep=time.sleep, now=None):
        self.store = store
        self.client = client or VisionClient()
        self.sleep = sleep
        self.now = now or (lambda: datetime.now(timezone.utc))

    def _complete(self, symbol, interval, spec):
        data = self.store.load(symbol, interval, spec.start_ts, spec.end_ts - 1)
        expected = (spec.end_ts - spec.start_ts) // CandleStore._step(interval)
        return len(data.timestamp) == expected and data.gaps == 0

    def run_symbol(self, symbol, *, role="operar", months=24, interval="5m", dry_run=False):
        symbol = str(symbol).strip().upper().replace("/", "")
        summary = {"symbol": symbol, "role": role, "files_downloaded": 0, "candles": 0,
            "rejected": 0, "gaps": 0, "missing_archives": 0, "resumed": 0, "errors": []}
        specs = list(archive_specs(symbol, interval, months, now=self.now()))
        step = CandleStore._step(interval)
        for index, spec in enumerate(specs):
            if not dry_run and self.store is not None and self._complete(symbol, interval, spec):
                summary["resumed"] += 1
                continue
            try:
                content = self.client.get_verified_zip(spec.url)
                if content is None:
                    summary["missing_archives"] += 1
                    continue
                parsed, parse_rejected = parse_vision_zip(content)
                end_closed = spec.end_ts - step
                rows = [row for row in parsed if spec.start_ts <= int(row["open_ts"]) <= end_closed]
                valid, rejected = CandleStore.validate_rows(interval, rows)
                summary["files_downloaded"] += 1
                summary["candles"] += len(valid)
                summary["rejected"] += parse_rejected + rejected
                if not dry_run and self.store is not None:
                    result = self.store.upsert(symbol, interval, rows, SOURCE, role=role)
                    summary["gaps"] += int(result["gaps_detected"])
            except ChecksumError as exc:
                summary["rejected"] += 1
                summary["errors"].append(str(exc))
            except Exception as exc:
                summary["errors"].append(f"{spec.url.rsplit('/', 1)[-1]}: {type(exc).__name__}: {exc}")
            if index < len(specs) - 1:
                self.sleep(.2)
        return summary


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Ruta separada de market_data.db")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--symbols", nargs="+", help="Pares, por ejemplo XRPUSDT BTCUSDT")
    selection.add_argument("--registro", action="store_true", help="Monedas activas del Coin Registry (solo lectura)")
    selection.add_argument("--lista-inicial", action="store_true",
        help="Usa las monedas de datos de la decisión B3, sin duplicar operar")
    selection.add_argument("--fijar-papeles", action="store_true",
        help="Alinea roles con Coin Registry y la lista inicial de datos")
    parser.add_argument("--role", choices=("operar", "datos"), default="operar")
    parser.add_argument("--months", type=int, default=24)
    parser.add_argument("--interval", default="5m")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv=None, *, client=None, sleep=time.sleep, now=None):
    args = parse_args(argv)
    if args.months <= 0:
        raise SystemExit("--months debe ser mayor que cero")
    registry_path = Path(__file__).resolve().parents[1] / "asple_predictor.db"
    operating = active_registry_symbols(registry_path) if (args.registro or args.lista_inicial or args.fijar_papeles) and registry_path.is_file() else []
    if args.registro and not registry_path.is_file():
        raise FileNotFoundError(f"No existe el Coin Registry: {registry_path}")
    if args.fijar_papeles:
        db_path = Path(args.db).expanduser().resolve()
        if not db_path.is_file():
            raise FileNotFoundError(f"No existe el almacén para fijar papeles: {db_path}")
        if args.dry_run:
            import sqlite3
            conn = sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)
            try:
                current = dict(conn.execute("SELECT symbol,role FROM symbols"))
            finally:
                conn.close()
            changes = role_fix_plan(operating, current)
            for symbol, previous, role in changes:
                print(f"ROLE_CHANGE symbol={symbol} from={previous} to={role} dry_run=1")
        else:
            store = CandleStore(db_path)
            current = store.symbol_roles()
            changes = role_fix_plan(operating, current)
            apply_role_fix_plan(store, changes)
            for symbol, previous, role in changes:
                print(f"ROLE_CHANGE symbol={symbol} from={previous} to={role} dry_run=0")
        return 0
    if args.lista_inicial:
        symbols_with_roles = initial_symbol_roles(operating)
    else:
        symbols = operating if args.registro else [symbol.upper().replace("/", "") for symbol in args.symbols]
        symbols_with_roles = [(symbol, args.role) for symbol in symbols]
    store = None if args.dry_run else CandleStore(args.db)
    runner = BackfillRunner(store, client, sleep=sleep, now=now)
    for symbol, role in symbols_with_roles:
        summary = runner.run_symbol(symbol, role=role, months=args.months,
            interval=args.interval, dry_run=args.dry_run)
        print("BACKFILL " + " ".join(f"{key}={value}" for key, value in summary.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
