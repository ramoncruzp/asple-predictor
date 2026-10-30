#!/usr/bin/env python3
"""Guarda filtros públicos de símbolos en market_data.db."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.market_store import CandleStore


def actualizar_filtros(store, client):
    """Actualiza los papeles operar/datos con un cliente inyectable."""
    symbols = store.filter_symbols()
    resultado = {"actualizados": 0, "sin_ficha": 0}
    for row in symbols:
        info = client.get_symbol_info(row["symbol"])
        if not info:
            resultado["sin_ficha"] += 1
            continue
        store.save_symbol_filters(row["symbol"], info)
        resultado["actualizados"] += 1
    return resultado


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    args = parser.parse_args(argv)
    from data.binance_client import BinanceClient
    store = CandleStore(args.db)
    result = actualizar_filtros(store, BinanceClient(None, None))
    print(f"FILTROS actualizados={result['actualizados']} sin_ficha={result['sin_ficha']}")
    try:
        import psutil
        print(f"RSS máximo estimado MB={psutil.Process().memory_info().rss / 1048576:.1f}")
    except ImportError:
        if os.name == "posix":
            import resource
            print(f"RSS máximo MB={resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.1f}")
        else:
            print("RSS máximo omitido (psutil no está instalado)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
