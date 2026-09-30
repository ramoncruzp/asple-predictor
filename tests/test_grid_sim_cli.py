from datetime import datetime, timezone
import json

from scripts.grid_sim import main


def _csv(path):
    with path.open("w", encoding="utf-8") as f:
        f.write("timestamp,open,high,low,close\n")
        for i in range(12):
            stamp = datetime.fromtimestamp(1_700_000_100 + i * 300, timezone.utc).isoformat()
            price = 100 + (i % 3 - 1) * .1
            f.write(f"{stamp},{price},{price+.2},{price-.2},{price}\n")


def test_run_and_compare_write_json(tmp_path, capsys):
    source, output = tmp_path / "candles.csv", tmp_path / "result.json"
    _csv(source)
    args = ["--start", "2023-11-14T22:15:00+00:00", "--end", "2023-11-14T23:10:00+00:00",
            "--n", "4", "--capital", "1000", "--width-pct", "10", "--csv", str(source), "--out", str(output)]
    assert main(["run", *args, "--strategy", "simple"]) == 0
    assert json.loads(output.read_text())["strategy"] == "simple"
    assert main(["compare", *args]) == 0
    assert "smart_minus_simple" in output.read_text()


def test_cli_invalid_usage_and_data_have_codes_without_tracebacks(tmp_path, capsys):
    assert main(["run", "--start", "x", "--end", "y", "--n", "4", "--capital", "10",
                 "--width-pct", "1", "--csv", str(tmp_path / "missing.csv")]) == 3
    text = capsys.readouterr().err
    assert "Traceback" not in text
    assert main(["run", "--start", "x", "--end", "y", "--n", "4", "--capital", "10",
                 "--width-pct", "-1"]) == 2
    assert "Traceback" not in capsys.readouterr().err
