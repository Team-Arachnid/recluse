"""Phase 1 -- temporal train/validation/test splits.

Day structure of CICIDS2017:

    Monday      benign only                                  autoencoder train
    Tuesday     FTP-Patator, SSH-Patator                     train
    Wednesday   DoS Hulk/GoldenEye/Slowloris/Slowhttptest,
                Heartbleed                                   train
    Thursday    web attacks (AM), infiltration (PM)          validation
    Friday      botnet, port scan, DDoS                      test

Temporal splits only. train_test_split(shuffle=True) leaks near-identical
duplicated flows across train and test and manufactures fake 99.9% scores.

Checkpoint for this phase: row counts per split per class, zero duplicate rows
shared across splits, and no NaN or Inf surviving.
"""

from __future__ import annotations


def main() -> None:
    raise NotImplementedError("split.py is implemented in Phase 1 (data and features).")


if __name__ == "__main__":
    main()
