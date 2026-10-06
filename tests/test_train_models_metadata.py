from scripts import train_models


def test_training_metrics_merge_only_replaces_selected_models():
    prior = {
        "symbol": "XRPUSDT", "interval": "1h", "trained_at": "old-run",
        "models": {"model_a": {"accuracy": 0.6825, "trained_at": "a-run"},
                   "model_b": {"accuracy": 0.4}},
    }
    result = train_models.merge_training_metrics(
        prior, {"trained_at": "new-run", "days": 730},
        {"model_b": {"accuracy": 0.7, "trained_at": "b-new-run"}},
    )
    assert result["trained_at"] == "new-run"
    assert result["days"] == 730
    assert "model_a" in result["models"]
    assert result["models"]["model_a"] == {"accuracy": 0.6825, "trained_at": "a-run"}
    assert result["models"]["model_b"]["trained_at"] == "b-new-run"


def test_invalid_or_missing_metrics_manifest_starts_empty(tmp_path):
    path = tmp_path / "metrics.json"
    assert train_models.read_existing_metrics(path) == {}
    path.write_text("{broken", encoding="utf-8")
    assert train_models.read_existing_metrics(path) == {}


def test_training_outputs_replace_artifacts_before_metrics(tmp_path, monkeypatch):
    artifact_tmp = tmp_path / "model_b.pt.tmp"
    artifact = tmp_path / "model_b.pt"
    metrics_tmp = tmp_path / "metrics.json.tmp"
    metrics = tmp_path / "metrics.json"
    for path, body in ((artifact_tmp, "weights"), (metrics_tmp, "manifest")):
        path.write_text(body, encoding="utf-8")
    calls = []
    real_replace = train_models.os.replace

    def track_replace(src, dst):
        calls.append(str(dst))
        return real_replace(src, dst)

    monkeypatch.setattr(train_models.os, "replace", track_replace)
    train_models.replace_training_outputs({"b": artifact_tmp}, {"b": artifact}, metrics_tmp, metrics)
    assert calls == [str(artifact), str(metrics)]
    assert artifact.read_text(encoding="utf-8") == "weights"
    assert metrics.read_text(encoding="utf-8") == "manifest"


def test_main_preserves_model_returned_trained_at_in_manifest(tmp_path, monkeypatch):
    import json
    from argparse import Namespace
    from types import SimpleNamespace

    script = tmp_path / "scripts" / "train_models.py"
    script.parent.mkdir()
    script.touch()
    candles = tmp_path / "candles.csv"
    candles.write_text("timestamp,close\n2026-01-01T00:00:00Z,1.0\n2026-01-02T00:00:00Z,1.1\n", encoding="utf-8")
    returned_time = "2026-01-02T00:00:00Z"

    class FakeModel:
        def train(self, _frame):
            return {"accuracy": .5, "baseline_accuracy": .5, "roc_auc": .5,
                    "signal_coverage": .1, "signal_accuracy": None, "trained_at": returned_time}

        def save(self, path):
            from pathlib import Path
            Path(path).write_bytes(b"fake weights")

    args = Namespace(symbol="XRPUSDT", interval="1h", days=365, models=["b"], force=False,
                     candles=candles, save_candles=None, progress=False)
    monkeypatch.setattr(train_models, "__file__", str(script))
    monkeypatch.setattr(train_models, "parse_args", lambda: args)
    original_import = train_models.importlib.import_module
    monkeypatch.setattr(train_models.importlib, "import_module",
                        lambda name: SimpleNamespace(ModelB=FakeModel) if name == "models.model_b_gru" else original_import(name))
    assert train_models.main() == 0
    manifest = json.loads((tmp_path / "models" / "saved" / "metrics_xrp_1h.json").read_text(encoding="utf-8"))
    assert manifest["models"]["model_b"]["trained_at"] == returned_time
