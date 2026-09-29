# Model artifacts

Everything in this directory is produced by `backend/training/` on this
machine and is gitignored. It is reproducible output, not source.

| File                          | Written by                       | Phase |
| ----------------------------- | -------------------------------- | ----- |
| `preprocessing.pkl`           | `preprocess.py`, then rewritten by `train_supervised.py` with the champion's own bundle | 1, 2 |
| `supervised_model.pkl`        | `train_supervised.py`            | 2     |
| `supervised_<algorithm>.pkl`  | `train_supervised.py`            | 2     |
| `preprocessing_<algorithm>.pkl` | `train_supervised.py`          | 2     |
| `model_card.json`             | `train_supervised.py`, extended by `evaluate.py` and `train_autoencoder.py` | 2, 3 |
| `training_<algorithm>.json`   | `train_supervised.py`            | 2     |
| `metrics_supervised.json`     | `evaluate.py`                    | 2     |
| `autoencoder.pt`              | `train_autoencoder.py`           | 3     |
| `training_autoencoder.json`   | `train_autoencoder.py`           | 3     |
| `metrics_anomaly.json`        | `train_autoencoder.py`           | 3     |

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

`autoencoder.pt` is a **bare state dict** — weights and nothing else. That is
what lets `app/inference.py` read it with `weights_only=True`, which makes the
file data rather than code, and it is why `tau_anom` and the benign error
histogram live on the model card instead of inside it. The architecture is not
recorded twice either: `Autoencoder.from_state_dict` reads the layer widths back
out of the tensors, so what the card says about the architecture can only ever be
a readout of the file that shipped.

`model_card.json` carries both stages. Phase 2 writes it, `evaluate.py` adds the
test-day block, and `train_autoencoder.py` adds `thresholds.tau_anom`,
`anomaly_algorithm` and a `stage2` block holding Stage 2's own version, its
threshold record, the benign error histogram as bins, and the baseline
comparison. Stage 1's entries are never rewritten by Stage 2 — the card is one
record of one served pair, not two competing ones.

Nothing here is ever loaded from an untrusted source: the API has no artifact
upload path, and torch weights are read with `weights_only=True`.
