# Model artifacts

Everything in this directory is produced by `backend/training/` on this
machine and is gitignored. It is reproducible output, not source.

| File                  | Written by                    | Phase |
| --------------------- | ----------------------------- | ----- |
| `preprocessing.pkl`   | `split.py` / `features.py`    | 1     |
| `supervised_model.pkl`| `train_supervised.py`         | 2     |
| `autoencoder.pt`      | `train_autoencoder.py`        | 3     |
| `model_card.json`     | `evaluate.py`                 | 2/3   |

`preprocessing.pkl` must contain, together in one bundle:

```python
{
    "scaler": fitted_scaler,  # RobustScaler, fit on train only
    "feature_order": [...],  # exact column order
    "dropped_columns": [...],
    "port_encoding": {...},
    "schema_hash": "sha256:...",  # checked at startup
}
```

The scaler, the column order and the hash travel together because none of them
alone is enough to reproduce the training-time feature matrix. `app/inference.py`
recomputes the hash from `feature_order` at startup and refuses to serve on a
mismatch — train/serve skew produces garbage scores without raising anything, so
the check has to be the thing that makes noise.

Nothing here is ever loaded from an untrusted source: the API has no artifact
upload path, and torch weights are read with `weights_only=True`.
