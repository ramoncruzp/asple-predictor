import math
import re
import sqlite3

from scripts.escenarios_paridad import main


def _market_db(path):
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE symbols(id INTEGER PRIMARY KEY, symbol TEXT, role TEXT, active INTEGER);
            CREATE TABLE candles(symbol_id INTEGER, interval TEXT, open_ts INTEGER,
                open REAL, high REAL, low REAL, close REAL);
            INSERT INTO symbols VALUES(1, 'SYNTHUSDT', 'operar', 1);
        """)
        rows = []
        previous = 100.0
        for index in range(20 * 288):
            close = 100 + 5 * math.sin(index / 50)
            rows.append((1, "5m", index * 300, previous,
                max(previous, close) + .1, min(previous, close) - .1, close))
            previous = close
        conn.executemany("INSERT INTO candles VALUES(?,?,?,?,?,?,?)", rows)


def test_parity_script_runs_against_temporary_synthetic_database(tmp_path, capsys):
    db_path = tmp_path / "market_data_synthetic.db"
    _market_db(db_path)
    assert main(["--db", str(db_path), "--casos", "1", "--semilla", "43"]) == 0
    output = capsys.readouterr().out
    assert "casos_completados=1 discrepancias=0" in output
    rss = re.search(r"rss_max_mb=([0-9.]+)", output)
    assert rss and float(rss.group(1)) > 0
