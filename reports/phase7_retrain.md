# Phase 7 -- retraining from analyst verdicts

Measured 2026-10-07T00:29:30+00:00.

## What the analysts supplied

```
false positives   13  -> benign training rows
true positives    14  -> attack rows, labelled by family
unnamed attacks   7  -> confirmed attacks with no family; left out
undecided         0  -> recorded, not trained on
```

A false positive is the label that moves the model: a flow the classifier
called an attack and a human called benign is a hard negative, which is the
kind of example that moves a boundary. A confirmed true positive mostly
agrees with what the model already did.

The unnamed attacks are the honest hole in the loop. They are the most
valuable labels a SOC produces -- traffic Stage 2 caught that Stage 1 could
not name -- and they cannot enter a classifier that is multiclass by family
until a human says which family. Guessing would fabricate the one thing
nobody said.

## The benign refit pool, and its guards

```
candidates        13  (analyst-confirmed false positives)
admitted          1
refused by cap    12  (no host above 20% of the pool, which solved to 1 row(s) per host)
usable            False  -- 1 rows is under the 200-row floor, so the baseline is left alone. A baseline moved by a handful of rows is a baseline moved by whoever supplied them.

HOSTS AT THE CAP
  172.16.0.1                1 admitted,     12 refused
```

Nothing enters that pool without an explicit false-positive confirmation,
and no single source host may exceed its cap. Without both, anyone who can
generate enough traffic and get it waved through can teach the baseline
that their traffic is normal.

## Champion against challenger

```
gate              val (PR-AUC)
champion          stage1-lgbm-202610061904
challenger        stage1-lgbm-202610070027
control           stage1-lgbm-202610070028  (same config, no labels)

split         champion      control   challenger      delta
test           0.8792       0.8468       0.8685    -0.0107
val            0.8969       0.8816       0.8914    -0.0055  <- gate

vs champion       -0.0055  (should the challenger replace what is serving -- the gate)
vs control        +0.0099  (what the analyst labels were worth, everything else held constant)
                  the control did not land on the champion: a labelled challenger has been promoted before, so the gate compares two labelled models

decision          Not promoted: val PR-AUC moved by -0.0055 against the serving champion. The analyst labels were worth +0.0099 against a control fitted without them, with everything else held constant. The champion stays, the challenger stays on disk under its own name, and this row is the evidence the gate works.
```

**The gate is the val split.** Both splits are held out, but this is the
one that exists for model selection. Gating on the test day would make every
retrain a selection step on it, and the test numbers this project quotes
would creep upward over successive runs while describing less and less.

## A caveat this demo cannot design away

In a real deployment the analyst labels come from production traffic, and the
held-out splits stay untouched. Here they come from a *replay of a held-out
split*, because that is the only traffic this project has. So:

- Labels drawn from a replay of the **test** day leave the validation gate
  clean, which is why the promotion decision above is still sound.
- But the **test** numbers reported after such a retrain are no longer an
  unbiased estimate: some of those rows are now in the training set, with
  labels a human derived from the same ground truth the evaluation scores
  against.

Read the gate column as the decision and the test column as contaminated
once a retrain has consumed labels from the test day. The clean comparison
is the one in `reports/phase2_supervised.md`, measured before any feedback
existed.

The challenger did not improve on it: 0.8969 to 0.8914 (-0.0055).

## The control arm, and the two questions it separates

A third model is fitted in every run: the same configuration on the same
unaugmented data, by this code, now. It is not a candidate to serve. It is
there because there are two different questions here and only one of them
is the gate's:

- **Should the challenger replace what is serving?** challenger - champion.
  A deployment question, and the one promotion turns on.
- **Did the analyst labels teach the model anything?** challenger - control,
  with the data, the seed and the code held constant, so the only difference
  between the two fits is the labels.

On val: **-0.0055** against the champion, **+0.0099** against the control.

The control did not land on the champion, because a labelled challenger has been promoted before. The gate is therefore comparing two labelled models, which is exactly why the label effect has to be measured against the control rather than read off the gate.

An earlier version of this pipeline read the champion-to-control gap as a
run-to-run noise floor and gated on it. It is not one: the control reproduced
the original no-label fit to four decimal places, which is what a
deterministic fit does. That gap was the previous run's label effect wearing
the wrong name, and a gate justified by it would have been justified by a
quantity it does not measure.

`metrics_loao.json` carries a control fold for the same reason: an arm that is
not a candidate, kept because it is what makes the others interpretable.

Not promoted: val PR-AUC moved by -0.0055 against the serving champion. The analyst labels were worth +0.0099 against a control fitted without them, with everything else held constant. The champion stays, the challenger stays on disk under its own name, and this row is the evidence the gate works.
