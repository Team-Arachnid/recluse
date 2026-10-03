# Leave-one-attack-out

The question this answers: **how much of an attack family does the system catch when the classifier has never been shown that family?** Stage 1 is refitted with the family removed. Stage 2 is untouched, because it never saw an attack label of any kind. The Stage 2 column is the headline.

Champion `stage1-lgbm-202609281410` (`lgbm`), schema `sha256:7672483867812ed560e289d8af7febbe3c29cf86356789e98e7a2086b6797160`, measured 2026-10-03T07:53:08+00:00. `tau_anom` = 1.098113e-01, identical in every fold. `tau_sup` is re-cut per fold from the validation day's benign rows at a 3.20e-04 false-positive budget.

## The table

| Held-out family | Rows | Caught by Stage 1 | Caught by Stage 2 | Total recall | Missed |
| --- | --- | --- | --- | --- | --- |
| `dos` | 193,745 | 0.0% | **75.5%** | 75.5% | 24.5% |
| `ddos` | 128,014 | 38.0% | **20.7%** | 58.6% | 41.4% |
| `brute_force` | 9,150 | 0.0% | **0.2%** | 0.2% | 99.8% |
| `port_scan` | 90,694 | 0.6% | **0.1%** | 0.7% | 99.3% |
| `web_attack` | 2,154 | 88.6% | **4.6%** | 93.2% | 6.8% |
| `botnet` | 1,948 | 0.0% | **2.2%** | 2.2% | 97.8% |
| `infiltration` | 36 | 0.0% | **44.4%** | 44.4% | 55.6% |

Read the strongest row out loud: the system had never seen `dos` traffic and Stage 2 surfaced 75.5% of it in the cascade. 24.5% of that family still got through. Both halves of that sentence are the result.

One qualification on that row before it gets quoted: every `dos` flow scored here comes from `train`, and the training days are the days whose *benign* traffic fitted Stage 2. No model was trained on these attack rows -- Stage 1 had them removed from its fit and Stage 2 never saw an attack label at all -- so what is weaker here than a held-out day is the separation, not the hold-out.

## What each fold held out

Only 2 of the 7 measured families needed a refit, and that is the temporal split doing the work rather than a shortcut: the training days are Tuesday and Wednesday, so Stage 1's vocabulary is benign, DoS and brute force. Every other family lives on a day the classifier never trained on, which means it was already held out before this evaluation started. Refitting to remove zero rows and calling it a retrain would be theatre; the rows-removed column is here so the difference is visible.

| Held-out family | Rows on the training days | Rows removed from Stage 1's fit | Stage 1 refitted | Fold vocabulary | `tau_sup` |
| --- | --- | --- | --- | --- | --- |
| _none (control)_ | -- | 0 | yes | `benign`, `dos`, `brute_force` | 0.387908 |
| `dos` | 193,745 | 193,745 | yes | `benign`, `brute_force` | 0.051544 |
| `ddos` | 0 | 0 | no | `benign`, `dos`, `brute_force` | 0.387908 |
| `brute_force` | 9,150 | 9,150 | yes | `benign`, `dos` | 0.052161 |
| `port_scan` | 0 | 0 | no | `benign`, `dos`, `brute_force` | 0.387908 |
| `web_attack` | 11 | 0 | no | `benign`, `dos`, `brute_force` | 0.387908 |
| `botnet` | 0 | 0 | no | `benign`, `dos`, `brute_force` | 0.387908 |
| `infiltration` | 0 | 0 | no | `benign`, `dos`, `brute_force` | 0.387908 |

Genuinely refitted: `dos` and `brute_force`. Already held out by the split or the support floor: `ddos`, `port_scan`, `web_attack`, `botnet` and `infiltration`.

## What the recall cost

A recall figure with no false-positive rate beside it is not a result. The negative class is the `test` day's 375,238 benign flows -- the only traffic in the capture that Stage 1 was not trained on, Stage 2 was not fitted on, and neither threshold was cut on. The two PR-AUCs ask the same question without a threshold: how well does each stage's raw score separate this family from that benign traffic.

| Held-out family | Benign rows | Stage 1 FPR | Stage 2 FPR | Fused FPR | Alerts/analyst/hour | Stage 1 PR-AUC | Stage 2 PR-AUC |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `dos` | 375,238 | 4.00e-05 | 5.96e-02 | 5.96e-02 | 7,450.9 | 0.3665 | 0.8607 |
| `ddos` | 375,238 | 1.63e-04 | 5.96e-02 | 5.97e-02 | 7,465.6 | 0.9924 | 0.7631 |
| `brute_force` | 375,238 | 2.59e-04 | 5.96e-02 | 5.98e-02 | 7,476.3 | 0.0797 | 0.0899 |
| `port_scan` | 375,238 | 1.63e-04 | 5.96e-02 | 5.97e-02 | 7,465.6 | 0.2859 | 0.4353 |
| `web_attack` | 375,238 | 1.63e-04 | 5.96e-02 | 5.97e-02 | 7,465.6 | 0.9061 | 0.0315 |
| `botnet` | 375,238 | 1.63e-04 | 5.96e-02 | 5.97e-02 | 7,465.6 | 0.0062 | 0.0102 |
| `infiltration` | 375,238 | 1.63e-04 | 5.96e-02 | 5.97e-02 | 7,465.6 | 0.0002 | 0.0094 |

The budget those alerts/hour figures are measured against is 40 alerts per analyst per hour over an 8-hour shift -- 320 per day against 1,000,000 flows, which is where the 3.20e-04 false-positive budget comes from.

The alerts-per-hour column is the one to read carefully, and it is overwhelmingly Stage 2's. `tau_anom` is the brief's 99.5th percentile of benign reconstruction error -- a statement about what normal traffic looks like, made without reference to any attack, which is precisely what keeps Stage 2 honest. It is not a staffing decision, and it does not pretend to be one: Phase 3 measured the same benign distribution reaching the analyst budget only at its 99.968th percentile. The next section reports what Stage 2 catches at each of the two thresholds, so the gap is a trade-off somebody can decide rather than a number that looks like a defect.

## Stage 2 on its own

Two things the headline table deliberately does not say. First, its Stage 2 column is a *marginal* figure -- what Stage 2 adds on rows Stage 1 passed through -- and that is lower than Stage 2's own recall wherever the two stages agree about a flow, which on high-rate floods is most of the time. Second, that figure is measured at the shipped threshold; the last column is the same measurement at the threshold cut to fit the queue on its calibration day -- which, as the verdict below this table says, does not mean it fits the queue here. None of this varies by fold, because the autoencoder is the component the loop holds fixed, so it is measured once.

| Family | Rows | Stage 2 alone, at `tau_anom` | Stage 2 in the cascade | Stage 2 alone, at the budget threshold |
| --- | --- | --- | --- | --- |
| `dos` | 193,745 | 75.5% | 75.5% | 4.6% |
| `ddos` | 128,014 | 53.3% | 20.7% | 10.3% |
| `brute_force` | 9,150 | 0.2% | 0.2% | 0.0% |
| `port_scan` | 90,694 | 0.2% | 0.1% | 0.0% |
| `web_attack` | 2,154 | 4.6% | 4.6% | 0.0% |
| `botnet` | 1,948 | 2.2% | 2.2% | 0.0% |
| `infiltration` | 36 | 44.4% | 44.4% | 27.8% |
| _benign (false positives)_ | 375,238 | 6.0% | -- | 0.6% |
| _Alerts/analyst/hour_ | -- | **7,449** | -- | **705** |
| _against a budget of_ | -- | 40 | -- | 40 |

**The affordable threshold is bought with the recall.** Moving Stage 2 from the shipped percentile to the budget threshold divides its benign false-positive rate by 10.6 -- 6.0% of ordinary flows down to 0.6% -- and `dos` falls from 75.5% to 4.6% with it. `brute_force`, `port_scan`, `web_attack` and `botnet` fall to 0.0%: at that threshold Stage 2 finds essentially none of them.

**And it still does not fit the queue.** `budget_tau` is cut from the *validation* day's benign distribution at the analyst budget, so on that day it fits by construction. On this one it puts 705 alerts in front of each analyst per hour against a budget of 40 -- **17.6x over**. That is not a second defect; it is the same domain shift the next paragraph is about, now carrying its own number. A threshold cut on one day of one capture does not transfer to the next day of the same capture.

So the honest reading of both tables together is that neither threshold is a finished answer. The shipped one detects and overwhelms; the one cut to fit the queue on its calibration day detects very little and is over budget here anyway. The three things that actually move this are not threshold choices: **dedup**, which collapses a burst from one source into a single queue row with an occurrence count rather than one row per flow -- the per-analyst-hour projection above assumes one row per flow, which is the assumption Phase 5 removes; **risk ranking**, so the queue is worked in order of consequence instead of arrival; and **recalibration against a local benign baseline**, because this threshold was cut on one lab's Thursday and moving it to that lab's Friday multiplied its false-positive rate by 11.9. A threshold slider on the Live Traffic screen is where whoever owns the queue chooses a point on this curve, and nothing is auto-blocked at any setting.

## With the family in training, and without

The control is the same procedure with nothing removed, which is what makes the table a comparison rather than an assertion. For a family the temporal split already held out, the control *is* the held-out model and the row says so -- there is no before-and-after to show.

| Family | Stage 1, family in training | Named correctly | Stage 1, family held out | Named correctly | Stage 2, family held out | Total, family held out |
| --- | --- | --- | --- | --- | --- | --- |
| `dos` | 100.0% _(in-sample)_ | 100.0% | 0.0% | 0.0% | **75.5%** | 75.5% |
| `ddos` | 38.0% _(same model)_ | 0.0% | 38.0% | 0.0% | **20.7%** | 58.6% |
| `brute_force` | 100.0% _(in-sample)_ | 100.0% | 0.0% | 0.0% | **0.2%** | 0.2% |
| `port_scan` | 0.6% _(same model)_ | 0.0% | 0.6% | 0.0% | **0.1%** | 0.7% |
| `web_attack` | 88.6% _(same model)_ | 0.0% | 88.6% | 0.0% | **4.6%** | 93.2% |
| `botnet` | 0.0% _(same model)_ | 0.0% | 0.0% | 0.0% | **2.2%** | 2.2% |
| `infiltration` | 0.0% _(same model)_ | 0.0% | 0.0% | 0.0% | **44.4%** | 44.4% |

**Caught is not the same as named, and the two naming columns are why this matters.** Stage 1's score is the largest single attack-class probability, so a held-out family can clear `tau_sup` under a different family's label -- DDoS flows alerting as `dos` is the obvious case. That is still a correct alert about a flood, with the family one level off, and it is counted as caught for the same reason Phase 2 counts it: the analyst gets a true positive to work. But a held-out fold has no column for the family at all, so its naming rate is zero by construction. Where a held-out row shows Stage 1 recall above zero, read it as *an alert was raised on this flow under some other family's name*, never as classification.

## Reading the misses

**`dos`** -- 193,745 rows from `train`; Stage 1 0.0%, Stage 2 75.5%, missed 24.5%.

High-rate floods sit far outside the benign envelope on several features at once -- duration, packet rate, bytes per second -- so a reconstruction trained on ordinary traffic has nowhere to put them.

Stage 2 surfaced 75.5% of a family the classifier had never been shown, and 24.5% got through. That is the claim this project is making, with its cost attached.

**`ddos`** -- 128,014 rows from `test`; Stage 1 38.0%, Stage 2 20.7%, missed 41.4%.

Behaviourally the same shape as DoS from a single flow's point of view; the distribution is in the source addresses, which are not features. A classifier that has learned DoS will often name DDoS flows `dos`, and that counts as caught here: the analyst gets a correct alert about a flood, with the family one level off.

Stage 1 carried this row, not Stage 2: 38.0% of the family cleared `tau_sup` under a *related* family's label, which is generalisation inside the classifier rather than novel-attack detection. Stage 2 added 20.7% on top and 41.4% got through. Worth having, and not the claim the Stage 2 column is making.

**`brute_force`** -- 9,150 rows from `train`; Stage 1 0.0%, Stage 2 0.2%, missed 99.8%.

Many short, regular, near-identical sessions against one service port. Each flow on its own is a modest outlier at most -- the pattern is in the repetition, which a per-flow score cannot see.

Missed almost entirely -- 99.8% got through. This is a real limitation of flow-level detection rather than a tuning problem, and no threshold moves it.

**`port_scan`** -- 90,694 rows from `test`; Stage 1 0.6%, Stage 2 0.1%, missed 99.3%.

Single-packet flows with near-zero duration and almost no bytes. Statistically they are indistinguishable from the shortest ordinary flows, and the evidence for *scanning* is aggregate -- hundreds of distinct destination ports from one source inside a few seconds. This feature set is per-flow, so there is nothing in one row to find.

Missed almost entirely -- 99.3% got through. This is a real limitation of flow-level detection rather than a tuning problem, and no threshold moves it.

**`web_attack`** -- 2,154 rows from `train` and `val`; Stage 1 88.6%, Stage 2 4.6%, missed 6.8%.

SQL injection and XSS ride inside otherwise ordinary HTTP sessions. The flow statistics barely move, because the attack is in the payload and flow-level features cannot see payload content.

Stage 1 carried this row, not Stage 2: 88.6% of the family cleared `tau_sup` under a *related* family's label, which is generalisation inside the classifier rather than novel-attack detection. Stage 2 added 4.6% on top and 6.8% got through. Worth having, and not the claim the Stage 2 column is making.

**`botnet`** -- 1,948 rows from `test`; Stage 1 0.0%, Stage 2 2.2%, missed 97.8%.

Command-and-control beaconing is deliberately shaped to look like ordinary traffic -- that is the design goal of the malware. This is the case where *unusual* and *malicious* come apart furthest.

Largely missed: 97.8% of this family got through both stages. The number is in the table because leaving it out would make the rest of the table less believable, not more.

Small sample: 1,948 rows. The 95% interval on that 2.2% total runs from 1.6% to 2.9% (Wilson), so read the figure as a range and not as a decimal place.

**`infiltration`** -- 36 rows from `val`; Stage 1 0.0%, Stage 2 44.4%, missed 55.6%.

A dropper followed by quiet internal activity: long, low-volume sessions. Some of that shape overlaps genuinely idle benign traffic, which is why the detection is partial rather than absent.

Partial: 44.4% caught, 55.6% missed. Worth having and not worth overselling -- a detector that finds a fifth to a half of an unseen family shortens an investigation rather than replacing one.

Small sample: 36 rows. The 95% interval on that 44.4% total runs from 29.5% to 60.4% (Wilson), so read the figure as a range and not as a decimal place.

## Method

**The fusion rule is the shipped one.** `training/fusion.py` is imported here and by `app/inference.py`; there is no second implementation written for the evaluation. Stage 1 names what clears `tau_sup`, everything else falls through to Stage 2, and the two columns are disjoint by construction.

**The feature contract is frozen to the champion's.** Stage 2's weights were fitted against one `RobustScaler`, so scoring them through a per-fold scaler would not be the same model -- and the brief requires the autoencoder to be unchanged. The residual, stated rather than hidden: the frozen scaler's medians and interquartile ranges were computed over the held-out family's rows as well. Those are column statistics, not labels, and no fold's classifier ever sees a row of the family it is holding out.

**No fold has a validation set.** Each fold trains for the champion's recorded iteration count with early stopping switched off, so a fold differs from the control in exactly one way. That count was chosen by the champion's own early stopping against the validation day, and it is the single thread connecting any fold to that day: one integer. Which way that integer points is worth stating too, because *one integer* reads as family-neutral and is not: the stopping rule maximised attack PR-AUC on the validation day, and the validation day's attack rows are `web_attack` and `infiltration` -- families in this very table. For those rows the iteration count was selected, in part, to detect them. `tau_sup` is re-cut per fold from benign rows only, which carries no information about any held-out family.

**A family's rows are all of its rows.** Each family is scored on every row of it in the capture rather than on a sample, so the recall figure is a statement about the family and not about a chosen subset. Where those rows came from is in the provenance above -- and for the families that live on the training days, they come from a day whose *benign* traffic was in training even though their attack rows were removed from the fit. That is a weaker temporal separation than a held-out day, and it applies to exactly those rows of the table.

**What this does not prove.** Leave-one-attack-out measures generalisation to held-out *known* attacks. It is a proxy for genuinely novel ones, not proof of them: these families existed in 2017 and were captured by the same lab, on the same network, as the benign baseline. The honest claim is that the system detects attack behaviour it was not trained to name -- which is what the Stage 2 column measures -- not that it will catch whatever arrives next.

Arena: 800,979 flows (375,238 benign + 425,741 attack). Full record in `backend/artifacts/metrics_loao.json`.
