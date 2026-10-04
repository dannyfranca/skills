from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmark_store import BenchmarkError
from comparison import measure


def main() -> None:
    parser = argparse.ArgumentParser(description='Measure saved benchmark evidence without model calls')
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--rates', type=Path)
    options = parser.parse_args()
    try:
        result = measure(options.run, rates_file=options.rates)
    except (BenchmarkError, OSError, ValueError, KeyError) as exc:
        parser.exit(2, f'{exc}\n')
    print(json.dumps({'file': str(options.run.resolve() / 'measurements.json'),
                      'executions': len(result['executions']), 'complete_pairs': sum(p['complete'] for p in result['paired'])}))


if __name__ == '__main__':
    main()
