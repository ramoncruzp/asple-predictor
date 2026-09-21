"""Train ModelD on real XRPUSDT 4H candles from Binance."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.binance_client import BinanceClient
from models.model_d_tft import ModelD


def main() -> None:
    candles = BinanceClient("", "").get_historical_klines("XRPUSDT", "4h", lookback_days=180).tail(1000).reset_index(drop=True)
    print(f"REAL_CANDLES: {len(candles)}")
    if len(candles) != 1000:
        raise RuntimeError(f"Se esperaban 1000 velas reales y se recibieron {len(candles)}")
    model = ModelD()
    metrics = model.train(candles)
    output = ROOT / "models" / "saved" / "model_d_xrp_4h.pt"
    model.save(str(output))
    print(f"METRICS: {metrics}")
    print(f"SAVED: {output}")
    print(f"SAVED_EXISTS: {output.exists()}")


if __name__ == "__main__":
    main()
