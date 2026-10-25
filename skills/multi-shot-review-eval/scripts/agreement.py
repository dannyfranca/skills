from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from analyst_files import AnalystFile, Verdict
from eval_errors import EvalError
from eval_inputs import AnalystInputs, SampleEntry, load_analyst_inputs


DECIDED = (Verdict.VALID, Verdict.INVALID)


@dataclass(frozen=True)
class PairAgreement:
    analysts: tuple[str, str]
    n: int
    agreement: float | None
    kappa: float | None

    def as_dict(self) -> dict[str, Any]:
        return {"analysts": list(self.analysts), "n": self.n, "agreement": self.agreement, "kappa": self.kappa}


@dataclass(frozen=True)
class ClassAccuracy:
    """Share of one finding class that an analyst confirmed; unjudgeable verdicts stay out of the denominator."""

    share: float | None
    n: int


def pairwise_agreement(analysts: Sequence[AnalystFile]) -> list[PairAgreement]:
    return [_pair(left, right) for left, right in combinations(analysts, 2)]


def cohens_kappa(pairs: Sequence[tuple[Verdict, Verdict]]) -> float | None:
    """Undefined when nothing was judged by both or both analysts always gave one verdict (chance agreement is 1)."""

    n = len(pairs)
    if n == 0:
        return None
    observed = sum(left == right for left, right in pairs) / n
    expected = sum(
        (sum(left == verdict for left, _ in pairs) / n) * (sum(right == verdict for _, right in pairs) / n)
        for verdict in DECIDED
    )
    if expected == 1:
        return None
    return (observed - expected) / (1 - expected)


def sample_accuracy(
    arms: Sequence[str], entries: Sequence[SampleEntry], analysts: Sequence[AnalystFile]
) -> dict[str, dict[str, dict[str, ClassAccuracy]]]:
    """Every arm gets a row, so an arm without sampled findings shows null shares instead of vanishing."""

    confirming = {"kept": Verdict.VALID, "rejected": Verdict.INVALID}
    return {
        arm: {
            analyst.analyst: {
                finding_class: _accuracy(
                    [analyst.judgments[entry.key].verdict for entry in entries
                     if entry.fields["arm"] == arm and entry.fields["finding_class"] == finding_class],
                    verdict,
                )
                for finding_class, verdict in confirming.items()
            }
            for analyst in analysts
        }
        for arm in arms
    }


def accuracy_document(accuracy: Mapping[str, Mapping[str, Mapping[str, ClassAccuracy]]]) -> dict[str, Any]:
    return {
        arm: {
            analyst: {
                "kept_valid": classes["kept"].share,
                "kept_n": classes["kept"].n,
                "rejected_invalid": classes["rejected"].share,
                "rejected_n": classes["rejected"].n,
            }
            for analyst, classes in by_analyst.items()
        }
        for arm, by_analyst in accuracy.items()
    }


def agreement_result(inputs: AnalystInputs) -> dict[str, Any]:
    return {
        "agreement": [pair.as_dict() for pair in pairwise_agreement(inputs.analysts)],
        "sample_accuracy": accuracy_document(sample_accuracy(inputs.scope.arms, inputs.entries, inputs.analysts)),
    }


def _pair(left: AnalystFile, right: AnalystFile) -> PairAgreement:
    pairs = [
        (verdict.verdict, right.judgments[key].verdict)
        for key, verdict in left.judgments.items()
        if _decided(verdict.verdict) and key in right.judgments and _decided(right.judgments[key].verdict)
    ]
    n = len(pairs)
    return PairAgreement(
        analysts=(left.analyst, right.analyst),
        n=n,
        agreement=sum(a == b for a, b in pairs) / n if n else None,
        kappa=cohens_kappa(pairs),
    )


def _decided(verdict: Verdict) -> bool:
    return verdict in DECIDED


def _accuracy(verdicts: Sequence[Verdict], confirming: Verdict) -> ClassAccuracy:
    decided = [verdict for verdict in verdicts if _decided(verdict)]
    share = sum(verdict == confirming for verdict in decided) / len(decided) if decided else None
    return ClassAccuracy(share=share, n=len(decided))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyst agreement and sample accuracy for one eval directory.")
    parser.add_argument("eval_dir")
    args = parser.parse_args(argv)
    try:
        inputs = load_analyst_inputs(Path(args.eval_dir).expanduser())
    except EvalError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(agreement_result(inputs), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
