# Destination-port ablation

`lgbm`, trained twice on CICIDS2017 Tuesday+Wednesday and scored on the Thursday validation day. Everything except the destination-port encoding is identical.

| Encoding | Features | Validation PR-AUC | ROC-AUC | tau_sup | FPR at tau | Attack recall at tau |
| --- | --- | --- | --- | --- | --- | --- |
| `raw` | 70 | **0.8724** | 0.9953 | 0.2849 | 3.18e-04 | 88.2% |
| `bucketed` | 92 | **0.8816** | 0.9965 | 0.3879 | 3.18e-04 | 87.6% |

Raw minus bucketed: **-0.0092 PR-AUC** (-1.0% relative).

The two encodings land close together, so the raw destination port is not carrying the model. The bucketed encoding is the safer default for traffic whose port assignments differ from this lab's.

The bucketed encoding replaces the raw port with its IANA service group (well-known / registered / ephemeral) plus a one-hot for the twenty most frequent ports, fitted on the training split alone so a port first seen at serving time cannot widen the feature matrix under a trained model.
