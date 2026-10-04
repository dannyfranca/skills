from __future__ import annotations

import json
import math
import re
from datetime import date, datetime
from pathlib import Path

from benchmark_store import BenchmarkError, digest, load


TOKEN_FIELDS = ('input', 'cached_input', 'cache_write', 'output')


def elapsed(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    try:
        value = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()
        return value if value >= 0 else None
    except (ValueError, TypeError):
        return None


def token(value) -> int | None:
    return value if type(value) is int and value >= 0 else None


def normalize(value: dict, harness: str | None) -> dict:
    if harness == 'codex':
        total, cached = token(value.get('input_tokens')), token(value.get('cached_input_tokens'))
        return {'input': total - cached if total is not None and cached is not None and cached <= total else None,
                'cached_input': cached if total is not None and cached is not None and cached <= total else None,
                'cache_write': 0, 'output': token(value.get('output_tokens')),
                'semantics': 'Codex input includes cached input; cache writes have no separate field'}
    if harness == 'claude-code':
        return {'input': token(value.get('input_tokens')), 'cached_input': token(value.get('cache_read_input_tokens')),
                'cache_write': token(value.get('cache_creation_input_tokens')), 'output': token(value.get('output_tokens')),
                'semantics': 'Claude input, cache reads, and cache writes are separate fields'}
    return {**{field: None for field in TOKEN_FIELDS}, 'semantics': 'Unknown harness accounting'}


def events(text: str) -> list[dict]:
    try:
        value = json.loads(text)
        return [value] if isinstance(value, dict) else []
    except ValueError:
        result = []
        for line in text.splitlines():
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                result.append(value)
        return result


def coverage(values: list) -> dict:
    known = [v for v in values if v is not None]
    return {'known_subtotal': sum(known) if known else None,
            'total': sum(known) if values and len(known) == len(values) else None,
            'known_records': len(known), 'total_records': len(values)}


def milliseconds(value) -> float | None:
    return value / 1000 if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def envelope(value: dict, harness: str | None, source: Path) -> dict:
    format_harness = harness or ('codex' if value.get('type') == 'turn.completed' else 'claude-code' if value.get('type') == 'result' else None)
    raw_usage = value.get('usage') if isinstance(value.get('usage'), dict) else {}
    models = []
    if format_harness == 'claude-code' and isinstance(value.get('modelUsage'), dict) and value['modelUsage']:
        for model, fields in value['modelUsage'].items():
            raw = fields if isinstance(fields, dict) else {}
            renamed = {key: raw.get(native) for key, native in [('input_tokens', 'inputTokens'), ('output_tokens', 'outputTokens'),
                       ('cache_read_input_tokens', 'cacheReadInputTokens'), ('cache_creation_input_tokens', 'cacheCreationInputTokens')]}
            models.append({'model': model, 'raw': fields, **normalize(renamed, format_harness)})
    else:
        models.append({'model': None, 'raw': raw_usage, **normalize(raw_usage, format_harness)})
    return {'raw': raw_usage, 'raw_model_usage': value.get('modelUsage'),
            **{field: coverage([r[field] for r in models])['total'] for field in TOKEN_FIELDS},
            'models': models, 'accounting_format': format_harness, 'source': str(source),
            'wall_seconds': milliseconds(value.get('duration_ms')), 'api_seconds': milliseconds(value.get('duration_api_ms')),
            'is_error': value.get('is_error'), 'result_type': value.get('subtype'),
            'semantics': 'Model usage takes precedence over top-level usage; the two are never added'}


def read_usage(stdout: Path, stderr: Path, harness: str | None) -> dict:
    sources, records, summaries = [], [], []
    for path in (stdout, stderr):
        if not path.is_file():
            continue
        raw = path.read_bytes()
        text = raw.decode('utf-8', errors='replace')
        sources.append({'path': str(path), 'sha256': digest(raw)})
        found = []
        for value in events(text):
            if not isinstance(value.get('usage'), dict) and not isinstance(value.get('modelUsage'), dict) and not any(milliseconds(value.get(f)) is not None for f in ('duration_ms', 'duration_api_ms')):
                continue
            if harness == 'codex' and value.get('type') != 'turn.completed':
                continue
            if harness == 'claude-code' and value.get('type') not in (None, 'result'):
                continue
            found.append(envelope(value, harness, path))
        if found and not records:
            records = found
        summaries.extend({'tokens': int(m.group(1).replace(',', '')), 'source': str(path)}
                         for m in re.finditer(r'tokens used\s*\n\s*([\d,]+)', text))
    totals = {field: coverage([r[field] for r in records])['total'] for field in TOKEN_FIELDS}
    return {'records': records, 'tokens': totals, 'reported_summaries': summaries, 'sources': sources,
            'runtime': {'wall_seconds': coverage([r['wall_seconds'] for r in records]),
                        'api_seconds': coverage([r['api_seconds'] for r in records]), 'basis': 'Harness duration_ms and duration_api_ms, converted to seconds'},
            'coverage': 'structured' if records else 'summary-only' if summaries else 'missing',
            'limitation': 'Recorded usage covers available harness events; it is not an invoice or proof of complete billing'}


def read_rates(path: Path | None) -> dict | None:
    if path is None:
        return None
    value = load(path)
    required = {'currency', 'units', 'source', 'date', 'assumptions', 'models'}
    if not isinstance(value, dict) or set(value) != required or value['units'] != 'per_million_tokens':
        raise BenchmarkError('Rates need currency, per_million_tokens units, source, date, assumptions, and models')
    if not all(isinstance(value[k], str) and value[k].strip() for k in ('currency', 'source', 'date', 'assumptions')):
        raise BenchmarkError('Rate provenance and assumptions must be nonempty text')
    try:
        date.fromisoformat(value['date'])
    except ValueError as exc:
        raise BenchmarkError('Rate date must use YYYY-MM-DD') from exc
    if not isinstance(value['models'], dict):
        raise BenchmarkError('Rates must map harness/model to token rates')
    for name, rates in value['models'].items():
        if not isinstance(name, str) or '/' not in name or not isinstance(rates, dict) or set(rates) != set(TOKEN_FIELDS):
            raise BenchmarkError('Each harness/model needs input, cached_input, cache_write, and output rates')
        if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in rates.values()):
            raise BenchmarkError('Token rates must be finite nonnegative numbers')
    return {**value, 'file': str(path.resolve()), 'sha256': digest(path.read_bytes())}


def cost(records: list[dict], rates: dict | None) -> dict:
    priced = []
    for record in records:
        items = [(event, model) for event in record['usage']['records'] for model in event['models']]
        if not items:
            items = [({}, {})]
        for index, (event, tokens) in enumerate(items, 1):
            price = None
            harness = record.get('harness') or event.get('accounting_format')
            model = tokens.get('model') or record.get('model')
            table = rates['models'].get(f'{harness}/{model}') if rates else None
            reason = 'No supplied rates' if rates is None else 'Unknown harness or model' if not harness or not model else 'No matching model rate' if table is None else 'Missing structured token fields' if any(tokens.get(f) is None for f in TOKEN_FIELDS) else None
            if reason is None:
                price = sum(tokens[f] * table[f] / 1_000_000 for f in TOKEN_FIELDS)
            priced.append({'id': record['id'], 'component': index, 'role': record['role'], 'harness': harness, 'model': model,
                           'estimate': price, 'unknown_reason': reason, 'source': event.get('source')})
    known = [r['estimate'] for r in priced if r['estimate'] is not None]
    complete = bool(priced) and len(known) == len(priced)
    return {'currency': rates['currency'] if rates else None, 'rate_provenance': rates,
            'estimate': sum(known) if complete else None, 'known_subtotal': sum(known) if known else None,
            'priced_records': len(known), 'total_records': len(priced), 'coverage_unit': 'Model usage components, with one unknown component for each missing role record',
            'complete': complete, 'records': priced,
            'limitation': 'Estimate uses recorded events and supplied rates; missing records and provider charges remain unknown'}
