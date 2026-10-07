# Phase 2 — Stage 1, the supervised classifier

Model `stage1-lgbm-202610070057` (`lgbm`, destination port `bucketed`), measured on the held-out **Friday** test day: 595,894 flows, 220,656 of them attacks.

The test day was opened once, after the depth, the port encoding and the operating threshold had all been settled on the Thursday validation day.

## What Stage 1 was trained on

Classes in its vocabulary: `benign`, `dos`, `brute_force`.

```
benign            821,166
    BENIGN                          821,166
dos               193,745
    DoS Hulk                        172,846
    DoS GoldenEye                    10,286
    DoS slowloris                     5,385
    DoS Slowhttptest                  5,228
brute_force         9,150
    FTP-Patator                       5,931
    SSH-Patator                       3,219
web_attack             11  [held out: below the support floor]
    Heartbleed                           11
support floor    100 rows -- web_attack excluded from Stage 1's vocabulary
```

Training rows: 1,024,061 across 92 features, schema `sha256:7672483867812ed560e289d8af7febbe3c29cf86356789e98e7a2086b6797160`.

Held out of the vocabulary by the support floor (100 rows): `web_attack`. A class with a handful of examples is not a class a tree ensemble can learn, and under `class_weight="balanced"` it would earn a weight in the thousands and distort the whole decision surface. Those rows are still scored — being unnameable by Stage 1 is the condition Stage 2 exists for.

## The operating threshold

```
C                   = 40 alerts/hour
analyst_shift_hours = 8 hours
max_alerts_per_day  = 320 alerts/day
V                   = 1,000,000 flows/day
target_FPR          = 3.20e-04
tau_sup             = 0.387908
```

`tau_sup` is the smallest threshold whose false-positive rate on the validation day fits that budget. It is not 0.5 and it is not `argmax`.

| Measured on the test day | Value |
| --- | --- |
| False-positive rate at `tau_sup` | 1.63e-04 |
| Projected false alerts/day at V = 1,000,000 | 163 |
| **Alerts per analyst per hour** | **20.3** |
| Analyst budget | 320/day |
| Alert rate measured on this split | 8.25e-02 |

The projection is the false-positive rate multiplied by V, not the alert rate multiplied by V. This day is 37% attack traffic; projecting that density onto a real network's million flows would describe a queue no real network produces. True positives sit on top of the false-alert floor, and how many there are depends on how much attack traffic the network actually carries — which a lab capture cannot tell you.

## Per-class results on the test day

Predictions are taken at the operating point — a family is emitted only when the attack confidence clears `tau_sup` — because that is what the served system does.

| Class | Precision | Recall | F1 | Support | In Stage 1's vocabulary |
| --- | --- | --- | --- | --- | --- |
| `benign` | 0.686 | 1.000 | 0.814 | 375,238 | yes |
| `dos` | 0.000 | 0.000 | 0.000 | 0 | yes |
| `ddos` | 0.000 | 0.000 | 0.000 | 128,014 | **no — never trained on** |
| `brute_force` | 0.000 | 0.000 | 0.000 | 0 | yes |
| `port_scan` | 0.000 | 0.000 | 0.000 | 90,694 | **no — never trained on** |
| `botnet` | 0.000 | 0.000 | 0.000 | 1,948 | **no — never trained on** |

A class in the vocabulary with zero support is not a failure: the test day simply carries none of it. Its precision column still means something, though — it is the share of rows the model gave that name to which really were that family, and a zero there says every such prediction was a family the model does not have a name for. The confusion matrix below says which one.

## Confusion matrix

| true \ predicted | `benign` | `dos` | `ddos` | `brute_force` | `port_scan` | `botnet` |
| --- | --- | --- | --- | --- | --- | --- |
| `benign` | 375,177 | 59 | 0 | 2 | 0 | 0 |
| `dos` | 0 | 0 | 0 | 0 | 0 | 0 |
| `ddos` | 79,414 | 48,600 | 0 | 0 | 0 | 0 |
| `brute_force` | 0 | 0 | 0 | 0 | 0 | 0 |
| `port_scan` | 90,193 | 254 | 0 | 247 | 0 | 0 |
| `botnet` | 1,948 | 0 | 0 | 0 | 0 | 0 |

## Detection view: does it flag the row at all

The table above scores Stage 1 on *naming* the family. The fusion pipeline asks a smaller question first — does anything fire — and a DDoS flow flagged as `dos` is caught even though the per-class table scores it as a misclassification.

| Family | Rows on this day | Flagged by Stage 1 | Recall |
| --- | --- | --- | --- |
| `ddos` | 128,014 | 48,600 | **38.0%** |
| `port_scan` | 90,694 | 501 | **0.6%** |
| `botnet` | 1,948 | 0 | **0.0%** |

| Metric | Value |
| --- | --- |
| **PR-AUC (headline)** | **0.8468** |
| ROC-AUC | 0.8820 |
| PR-AUC using `1 - P(benign)` instead | 0.8476 |
| Accuracy *(table cell only, never a headline)* | 0.6296 |

ROC's false-positive rate is divided by the benign count, so on traffic that is almost all benign thousands of false alerts barely move it. Precision has no such denominator and every false positive costs it immediately. The curve points are persisted alongside this report, in the metrics JSON named after the model, so the dashboard can draw the two side by side.

| Split | Benign share | PR-AUC | ROC-AUC | Gap |
| --- | --- | --- | --- | --- |
| Validation (Thursday) | 99.5% | 0.8816 | 0.9965 | 0.1149 |
| Test (Friday) | 63.0% | 0.8468 | 0.8820 | 0.0352 |

That is the whole argument in two rows. On Thursday, where attacks are a rounding error in the traffic, ROC-AUC reads near-perfect while PR-AUC is far lower — and PR-AUC is the one that corresponds to an analyst's experience of the queue. On Friday the attack share is high enough that the two nearly agree, which is exactly why a single ROC figure quoted without the class balance beside it says very little.

Accuracy is 63.0% on a day that is 63.0% benign. A model that answered benign to everything would score about the same, which is why the number is in a table cell and nowhere else.

## Interpretation

Every attack family on this day — `ddos`, `port_scan`, `botnet` — is absent from Stage 1's training vocabulary, because CICIDS2017 runs each family on a single capture day and the split is temporal. The per-class recall for those rows is therefore not a measure of a model that tried and failed to name them; it is a structural zero. What Stage 1 can still do is recognise those rows as *some* attack when they resemble a family it does know, and at `tau_sup = 0.3879` it flags 38.0% of `ddos` and 0.0% of `botnet` — 22.3% of the day's attack traffic in total. The classes it handles worst are the ones whose flow shape has no analogue in Tuesday's brute-force traffic or Wednesday's denial-of-service traffic; the ones it handles best are the ones that do.

The more interesting number is the one those two disagree with. PR-AUC on this day is 0.8468, which says the model *ranks* Friday's attacks well above its benign traffic even though it cannot name a single one of them. Recall at `tau_sup` is low not because the ranking is poor but because the threshold sits where the false-positive budget put it, and most of those attacks rank below that line. Recall and queue volume are the same dial: buying more of the first spends more of the second, which is the trade-off the threshold slider on the Live Traffic screen exists to make visible to whoever actually owns it.

Both readings point the same way. A supervised stage cannot name what it was never shown, and on a temporal split that is most of the test day; the 77.7% of attack traffic it does not surface is the volume Stage 2 has to account for, and Phase 4 measures that per family rather than asserting it.

## What this hands to Phase 3

A promoted `supervised_model.pkl` paired with the `preprocessing.pkl` it was fitted against, both carrying the same schema hash, plus `tau_sup` travelling inside the model artifact rather than beside it.

The `Flagged by Stage 1` column above is the Stage 1 column of the Phase 4 leave-one-attack-out table. Stage 2 trains on the benign-only split Phase 1 already wrote and asserted attack-free, so it needs nothing from this model except the feature contract they share — which is the point of `features.py` being one module.

## Appendix: threshold candidates on the validation day

| tau | FPR | Attack recall | Alerts/day at V |
| --- | --- | --- | --- |
| 0.1000 | 3.58e-04 | 90.4% | 358 |
| 0.2500 | 3.33e-04 | 88.2% | 333 |
| 0.5000 | 2.95e-04 | 87.2% | 295 |
| 0.7500 | 2.25e-04 | 83.2% | 225 |
| 0.9000 | 2.07e-04 | 80.2% | 207 |
| 0.3879 | 3.18e-04 | 87.6% | 318 |
