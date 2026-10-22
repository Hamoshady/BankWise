"""Merging of front-side and back-side extraction results.

Only fields present on both sides need real merging (the national id is
printed on both). The rules never let a weaker read replace a stronger one,
and never pick between two disagreeing valid values.
"""
from __future__ import annotations

from typing import Dict

from .extract import STATUS_RANK, FieldResult, SideResult


def _merge_field(a: FieldResult, b: FieldResult) -> FieldResult:
    """Merge the same field read on two different sides (a = front, b = back)."""
    cands = a.candidates + b.candidates
    if a.value and b.value:
        if a.value == b.value:
            out = FieldResult(a.name, "both", a.value, a.status if STATUS_RANK[a.status] <= STATUS_RANK[b.status] else b.status,
                              min(x for x in (a.confidence, b.confidence) if x is not None), cands,
                              list(dict.fromkeys(a.flags + b.flags + ["confirmed_on_both_sides"])))
            return out
        return FieldResult(a.name, "both", None, "conflict", None, cands,
                           list(dict.fromkeys(a.flags + b.flags + ["cross_side_conflict"])))
    if a.value or b.value:
        src = a if a.value else b
        other = b if a.value else a
        status = src.status
        flags = list(dict.fromkeys(src.flags + other.flags))
        if other.status == "conflict":
            # the other side disagreed with itself; this single read cannot be promoted above single_read
            status = "single_read" if STATUS_RANK[status] > STATUS_RANK["single_read"] else status
            flags.append("other_side_unresolved")
        return FieldResult(src.name, src.side, src.value, status, src.confidence, cands, flags, src.source)
    # neither side has a value: keep the more informative failure
    worse = a if STATUS_RANK[a.status] >= STATUS_RANK[b.status] else b
    return FieldResult(worse.name, "both", None, worse.status, None, cands, list(dict.fromkeys(a.flags + b.flags)))


def merge_sides(front: SideResult, back: SideResult) -> SideResult:
    fields: Dict[str, FieldResult] = {}
    for name in list(dict.fromkeys(list(front.fields) + list(back.fields))):
        f, b = front.fields.get(name), back.fields.get(name)
        if f is not None and b is not None:
            fields[name] = _merge_field(f, b)
        else:
            fields[name] = f or b  # type: ignore[assignment]
    timings = {f"front_{k}": v for k, v in front.timings_ms.items()}
    timings.update({f"back_{k}": v for k, v in back.timings_ms.items()})
    return SideResult(side="both", fields=fields, all_text_lines=front.all_text_lines + back.all_text_lines,
                      timings_ms=timings)
