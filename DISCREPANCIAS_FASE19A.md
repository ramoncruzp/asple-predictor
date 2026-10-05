# Discrepancias — Fase 19A

La primera versión del prompt prohibía reentrenar y, por eso, la revisión inicial quedó detenida: los artefactos de producción se ajustaron con el 85 % de los datos (`scripts/train_vol_models.py:108-120`) y no eran válidos para elegir en VAL. La revisión posterior del 5-oct autorizó explícitamente ajustar los modelos solo en memoria con TRAIN 70 %. Se siguió esa autorización; los artefactos existentes no se usaron para pesos ni se modificaron.

`scripts/vol_research.py:82-108` ejecuta `chronological_split` y comparte el marco de features, pero su diccionario experimental contiene nueve modelos, incluido HARQ. `config/models_config.py:16-22` define los ocho modelos de esta fase y no incluye HARQ. El evaluador siguió `VOL_MODELS`; reutilizó `chronological_split`, `build_volatility_frame`, `feature_columns`, `score_forecast`, `_load_data` y `_new_model`, con VAL entregado a GBM/HAR_range para parada temprana/selección como hacen las clases existentes.

El evaluador añade p unilateral derivado de la misma distribución bootstrap para poder aplicar Holm; `models/volatility/evaluation.py` anteriormente devolvía IC y `beats`, pero no p. Los campos previos de la API de volatilidad siguen presentes; `consensus` solo se agrega para XRPUSDT y con pesos P. No hubo cambios de base de datos.

La primera ejecución del script encontró un NameError en el conteo de confianza; se corrigió y la ejecución posterior terminó código 0 y generó el JSON nuevo. Las tres mutaciones temporales fueron detectadas y sus archivos restaurados byte a byte. Ver detalles y resultados en `REPORTE_FASE19A.md`.
