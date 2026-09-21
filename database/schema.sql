CREATE TABLE IF NOT EXISTS predictions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, prediction_id TEXT UNIQUE NOT NULL,
 symbol TEXT NOT NULL, interval TEXT NOT NULL, model_name TEXT NOT NULL,
 predicted_at DATETIME NOT NULL, verify_at DATETIME NOT NULL,
 probability_up REAL NOT NULL, signal TEXT NOT NULL, confidence TEXT NOT NULL,
 features_snapshot TEXT, price_at_prediction REAL NOT NULL, market_condition TEXT
);
CREATE TABLE IF NOT EXISTS outcomes (
 id INTEGER PRIMARY KEY AUTOINCREMENT, prediction_id TEXT NOT NULL REFERENCES predictions(prediction_id),
 verified_at DATETIME NOT NULL, price_at_verification REAL NOT NULL, price_change_pct REAL NOT NULL,
 actual_direction TEXT NOT NULL, was_correct INTEGER NOT NULL, why_correct TEXT, why_wrong TEXT
);
CREATE TABLE IF NOT EXISTS model_accuracy_by_condition (
 id INTEGER PRIMARY KEY AUTOINCREMENT, model_name TEXT NOT NULL, condition_name TEXT NOT NULL,
 total_predictions INTEGER DEFAULT 0, correct_predictions INTEGER DEFAULT 0,
 accuracy REAL DEFAULT 0.0, last_updated DATETIME
);
CREATE INDEX IF NOT EXISTS ix_predictions_symbol_model ON predictions(symbol, model_name);
CREATE INDEX IF NOT EXISTS ix_predictions_verify_at ON predictions(verify_at);
CREATE INDEX IF NOT EXISTS ix_outcomes_prediction_id ON outcomes(prediction_id);
