from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from agreement import accuracy_document, pairwise_agreement, sample_accuracy
from dashboard import DashboardInput, render_dashboard
from eval_errors import EvalError
from eval_inputs import DASHBOARD_FILE, SYNTHESIS_FILE, load_analyst_inputs, load_synthesis


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render dashboard.html for one eval directory.")
    parser.add_argument("eval_dir")
    args = parser.parse_args(argv)
    try:
        result = _run(Path(args.eval_dir).expanduser())
    except EvalError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


def _run(eval_dir: Path) -> dict[str, Any]:
    inputs = load_analyst_inputs(eval_dir)
    synthesis = load_synthesis(eval_dir, inputs)
    agreement = pairwise_agreement(inputs.analysts)
    accuracy = sample_accuracy(inputs.scope.arms, inputs.entries, inputs.analysts)
    page = render_dashboard(
        DashboardInput(
            stats=inputs.stats,
            entries=inputs.entries,
            analysts=inputs.analysts,
            synthesis=synthesis,
            agreement=agreement,
            accuracy=accuracy,
            files=(*inputs.files, eval_dir / SYNTHESIS_FILE),
        )
    )
    path = eval_dir / DASHBOARD_FILE
    path.write_text(page, encoding="utf-8")
    return {
        "dashboard": str(path),
        "agreement": [pair.as_dict() for pair in agreement],
        "sample_accuracy": accuracy_document(accuracy),
    }


if __name__ == "__main__":
    sys.exit(main())
