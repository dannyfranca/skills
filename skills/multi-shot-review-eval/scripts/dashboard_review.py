from __future__ import annotations

from collections.abc import Mapping, Sequence

from agreement import ClassAccuracy, PairAgreement
from analyst_files import AnalystFile
from eval_inputs import SampleEntry
from html_parts import NA, notice, number, section, share, table, text


def finding_sample(entries: Sequence[SampleEntry], analysts: Sequence[AnalystFile]) -> str:
    cards = "".join(_entry(entry, analysts) for entry in entries)
    return section("finding-sample", "Finding sample", f"<p>{len(entries)} sampled findings.</p>{cards}")


def _entry(entry: SampleEntry, analysts: Sequence[AnalystFile]) -> str:
    fields = entry.fields
    facts = " · ".join(
        text(value)
        for value in (
            f"round {entry.round}",
            f"arm {fields['arm']}",
            fields["finding_class"],
            fields["severity"],
            fields["judgeability"],
            fields.get("location") or "no location",
        )
    )
    verdicts = "".join(
        f'<li><b>{text(analyst.analyst)}</b>: <span class="verdict {text(judgment.verdict)}">{text(judgment.verdict)}</span> {text(judgment.reason)}</li>'
        for analyst in analysts
        for judgment in [analyst.judgments[entry.key]]
    )
    return (
        f'<article class="finding {text(fields["finding_class"])}">'
        f"<h3>{text(fields['title'])}</h3><p class=\"facts\">{facts}</p>"
        f'<p class="facts">{text(entry.key[0])} {text(entry.key[1])}</p>'
        f"<ul class=\"verdicts\">{verdicts}</ul>"
        f"<details><summary>Finding content</summary><pre>{text(fields.get('content') or '')}</pre></details></article>"
    )


def analyst_views(
    analysts: Sequence[AnalystFile],
    agreement: Sequence[PairAgreement],
    accuracy: Mapping[str, Mapping[str, Mapping[str, ClassAccuracy]]],
) -> str:
    parts = [_analyst(analyst) for analyst in analysts]
    parts.append("<h3>Agreement</h3>")
    if agreement:
        parts.append(
            table(
                ["analysts", "n", "agreement", "kappa"],
                [[text(" vs ".join(pair.analysts)), number(pair.n), share(pair.agreement), number(pair.kappa)] for pair in agreement],
            )
        )
    else:
        parts.append(notice("info", "One analyst only; agreement needs two."))
    parts.append("<h3>Sample accuracy</h3>")
    parts.append(
        table(
            ["arm", "analyst", "kept judged valid", "kept n", "rejected judged invalid", "rejected n"],
            [
                [text(arm), text(analyst), share(classes["kept"].share), number(classes["kept"].n),
                 share(classes["rejected"].share), number(classes["rejected"].n)]
                for arm, by_analyst in accuracy.items()
                for analyst, classes in by_analyst.items()
            ],
        )
    )
    return section("analysts", "Analyst views", "".join(parts))


def _analyst(analyst: AnalystFile) -> str:
    rows = [[text(view.arm), text(view.proposal), text(view.confidence), text(view.reasoning)] for view in analyst.views]
    notes = text(analyst.notes) if analyst.notes else NA
    return (
        f'<div class="analyst"><h3>{text(analyst.analyst)} <small>effort {text(analyst.effort)}</small></h3>'
        f"{table(['arm', 'proposal', 'confidence', 'reasoning'], rows)}<p>Notes: {notes}</p></div>"
    )
