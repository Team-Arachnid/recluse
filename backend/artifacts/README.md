# Model artifacts

Everything in this directory is produced by `backend/training/` on this
machine and is gitignored. It is reproducible output, not source.

| File                          | Written by                       | Phase |
| ----------------------------- | -------------------------------- | ----- |
| `preprocessing.pkl`           | `preprocess.py`, then rewritten by `train_supervised.py` with the champion's own bundle | 1, 2 |
| `supervised_model.pkl`        | `train_supervised.py`            | 2     |
| `supervised_<algorithm>.pkl`  | `train_supervised.py`            | 2     |
| `preprocessing_<algorithm>.pkl` | `train_supervised.py`          | 2     |
| `model_card.json`             | `train_supervised.py`, extended by `evaluate.py`, `train_autoencoder.py` and `loao.py` | 2, 3, 4 |
| `training_<algorithm>.json`   | `train_supervised.py`            | 2     |
| `metrics_supervised.json`     | `evaluate.py`                    | 2     |
| `autoencoder.pt`              | `train_autoencoder.py`           | 3     |
| `training_autoencoder.json`   | `train_autoencoder.py`           | 3     |
| `metrics_anomaly.json`        | `train_autoencoder.py`           | 3     |
| `metrics_loao.json`           | `loao.py`                        | 4     |
| `drift_reference.json`        | `drift_reference.py`             | 7     |
| `challenger/`                 | `retrain.py`                     | 7     |
| `control/`                    | `retrain.py`                     | 7     |

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
comparison. Phase 4's `loao.py` adds a compact `loao` block: the per-family
hold-out numbers the dashboard panel draws, nothing more. Stage 1's entries are
never rewritten by Stage 2 or by Phase 4 — the card is one record of one served
pair, not three competing ones.

`metrics_loao.json` is the full hold-out record behind that block: the arena's
provenance, every fold's own threshold, the control fold, and the per-family
PR-AUCs. It is the file `GET /api/v1/metrics/model` reads when a client asks
for more than the panel. It carries counts and provenance and deliberately not
a copy of the arena's eight hundred thousand feature rows, and it is written
with `allow_nan=False` — `json.dumps` emits a bare `NaN` by default and every
strict parser downstream, the browser's included, then rejects the whole file.

`drift_reference.json` is the fixed baseline every PSI is computed against: per
feature, the quantile bin edges cut from the training split and the share of the
reference rows in each. It exists so the nightly drift job needs the artifact and
not the training data, which matters in a container that ships a model but not the
500MB dataset it was fitted on. It carries the `schema_hash` it was cut against,
and `training/drift_job.py` refuses a reference whose hash disagrees with the
loaded bundle's -- bins from a different feature order describe different features
under the same names, which would produce a confident, meaningless number.

JSON has no infinity, so the open outer edges are stored as `null` and restored on
read. That is not cosmetic: `json.dumps` emits a bare `Infinity`, which every
strict parser downstream rejects, and the edges have to be open so an observation
beyond the reference's range lands in a bin rather than being dropped. A value
outside the reference *is* drift, and discarding it would hide it by shrinking the
denominator.

`challenger/` and `control/` are two subdirectories each holding a full
`supervised_<algorithm>.pkl` / `preprocessing_<algorithm>.pkl` pair, written by a
retrain run. They are separate directories on purpose. The challenger is a
candidate to serve, so a run that declines to promote has to be a no-op on the
canonical pair rather than a restore of it. The control is not a candidate at all:
it is the same configuration refit on the same unaugmented data, and its distance
from the champion is the noise floor the promotion gate is read against. Keeping
them apart means neither can be mistaken for the other, or for what is serving.

Nothing here is ever loaded from an untrusted source: the API has no artifact
upload path, and torch weights are read with `weights_only=True`.
