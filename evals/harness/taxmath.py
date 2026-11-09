"""Arithmetic helpers for the sample-household evals."""

from __future__ import annotations


def fixture_capital_loss_rollforward(
    net_by_year: list[int], cap: int = 3000
) -> list[tuple[int, int, int]]:
    """Return (opening, deduction_used, closing) per year for the sample household.

    Fixture-specific, not a tax calculator. It assumes one filer unit (married
    filing jointly), all long-term results, no opening carryover in the first
    year, and enough taxable income that the annual cap is the only limit. It
    ignores the short-term/long-term carryover split and the taxable-income
    limit in the Capital Loss Carryover Worksheet.

    Each year's net gain first absorbs the opening carryover. Any remaining net
    loss is deducted up to ``cap`` (IRC 1211(b)) and the rest carries out.
    """
    rows: list[tuple[int, int, int]] = []
    opening = 0
    for net in net_by_year:
        remaining_loss = opening - net
        if remaining_loss <= 0:
            used = closing = 0
        else:
            used = min(cap, remaining_loss)
            closing = remaining_loss - used
        rows.append((opening, used, closing))
        opening = closing
    return rows
