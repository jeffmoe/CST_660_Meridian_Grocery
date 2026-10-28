"""Join row-count guard: planogram -> store_reference must stay one row per slot.

This is the control for last quarter's category-report incident. A duplicate
store_code in store_reference made the join fan out, silently doubling that
store's units sold in every category manager's morning report. A left join
catches fan-out; an inner join also catches slots dropped because their
store_code has no reference row.
"""

from __future__ import annotations

import pytest

from tests.reporting import print_violations


@pytest.mark.parametrize("how", ["left", "inner"])
def test_join_to_store_reference_preserves_row_count(planogram, store_reference, how):
    joined = planogram.reset_index().merge(
        store_reference, on="store_code", how=how, suffixes=("", "_store")
    )

    # Rows per source planogram row after the join: anything other than 1 is a violation.
    rows_after = joined.groupby("csv_line").size().reindex(planogram.index, fill_value=0)
    changed = planogram.assign(rows_after_join=rows_after)[rows_after != 1]
    rows_before, rows_joined = len(planogram), len(joined)
    print(f"planogram rows before join: {rows_before}; after {how} join: {rows_joined}")
    print_violations(f"planogram {how}-joined to store_reference keeps one row per slot", changed)

    if not changed.empty:
        keys = store_reference[store_reference["store_code"].isin(changed["store_code"])]
        print_violations("store_reference rows behind the change", keys.sort_values("store_code"))

    assert rows_joined == rows_before, (
        f"{how} join changed planogram row count from {rows_before} to {rows_joined}"
    )
