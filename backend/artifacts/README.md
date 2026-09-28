# Model artifacts

Everything in this directory is produced by `backend/training/` on this
machine and is gitignored. It is reproducible output, not source.

| File                          | Written by                       | Phase |
| ----------------------------- | -------------------------------- | ----- |
| `preprocessing.pkl`           | `preprocess.py`, then rewritten by `train_supervised.py` with the champion's own bundle | 1, 2 |
| `supervised_model.pkl`        | `train_supervised.py`            | 2     |
| `supervised_<algorithm>.pkl`  | `train_supervised.py`            | 2     |
| `preprocessing_<algorithm>.pkl` | `train_supervised.py`          | 2     |
| `model_card.json`             | `train_supervised.py`, extended by `evaluate.py` | 2 |
| `training_<algorithm>.json`   | `train_supervised.py`            | 2     |
| `metrics_supervised.json`     | `evaluate.py`                    | 2     |
| `autoencoder.pt`              | `train_autoencoder.py`           | 3     |

The canonical `supervised_model.pkl` and `preprocessing.pkl` are the champion,
copied together from whichever `supervised_<algorithm>.pkl` /
`preprocessing_<algorithm>.pkl` pair won on the validation day. Copying the pair
rather than the model alone is what keeps a model and its scaler from ever being
mismatched, and it makes falling back to the previous algorithm a file copy
rather than a retrain.

`supervised_model.pkl` is not a bare estimator. It is a dict carrying the fitted
`model`, the `classes` list whose order is the column order of `predict_proba`,
the `algorithm`, the `schema_hash` it was trained against, the `port_encoding`
and `tau_sup` itself — the threshold travels inside the model so the two cannot
be separated.

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
