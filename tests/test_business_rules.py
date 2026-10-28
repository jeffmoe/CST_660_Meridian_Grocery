"""Business-rule checks on store-level sales behaviour.

Units spike: flag any slot-day whose units_sold is more than 10x that slot's
median daily units over the window. A promotion legitimately lifts volume two
to three times, and a Saturday on promotion can reach five or six times a
quiet weekday, but a tenfold jump on an established item almost always means
a posting error (a quantity read with implied decimals, a batch loaded twice,
a weight item keyed as each) rather than real shopping. Left in, it distorts
promo-lift and replenishment decisions as badly as a join fan-out does.

Perishable waste: waste_units records product pulled past its sell-by date, so
it can only be non-zero on perishable SKUs. Waste on a shelf-stable item means
the store keyed a shrink event (damage, theft) under the wrong reason code.
"""

from __future__ import annotations

import pandas as pd

from tests.reporting import print_violations

MAX_MULTIPLE_OF_MEDIAN = 10

# A multiple of a tiny median is meaningless: a slow mover that sells 1 unit
# on a typical day and 12 when a restaurant buys a case is routine. Below this
# median the rule is not evaluated.
MIN_MEDIAN_UNITS = 3


def test_units_sold_within_10x_slot_median(tables):
    sales = tables["daily_sku_sales"].reset_index()
    planogram = tables["planogram"]

    sales["business_date"] = pd.to_datetime(sales["business_date"], errors="coerce")
    sales["units_sold"] = pd.to_numeric(sales["units_sold"], errors="coerce")
    window_start, window_end = sales["business_date"].min(), sales["business_date"].max()

    # Exclude slots set or removed inside the window. Their sales are dominated
    # by lifecycle events, not ordinary demand:
    #   * Cut-in: a new item sells slowly until shoppers find it, then ramps,
    #     so its median understates normal volume.
    #   * Discontinuation: the slot is run down and marked down to clear, so
    #     its last days are a clearance spike against a near-empty shelf.
    # The lifecycle dates themselves are covered by schema rules on set_date
    # and removal_date instead.
    set_date = pd.to_datetime(planogram["set_date"], errors="coerce")
    removal_date = pd.to_datetime(planogram["removal_date"], errors="coerce")
    in_window = set_date.between(window_start, window_end) | removal_date.between(window_start, window_end)
    # A planogram_id duplicated in planogram is excluded if any of its rows qualifies.
    lifecycle_slots = set(planogram.loc[in_window, "planogram_id"])
    print(f"window {window_start.date()} to {window_end.date()}; "
          f"excluding {len(lifecycle_slots)} slot(s) set or removed in the window")

    steady = sales[~sales["planogram_id"].isin(lifecycle_slots)].copy()
    steady["slot_median_units"] = steady.groupby("planogram_id")["units_sold"].transform("median")
    evaluated = steady[steady["slot_median_units"] >= MIN_MEDIAN_UNITS].copy()
    evaluated["multiple"] = evaluated["units_sold"] / evaluated["slot_median_units"]

    spikes = evaluated[evaluated["multiple"] > MAX_MULTIPLE_OF_MEDIAN]
    report = spikes.set_index("csv_line")[
        ["planogram_id", "business_date", "units_sold", "slot_median_units", "promo_flag", "multiple"]
    ].assign(multiple=lambda d: d["multiple"].round(1).astype(str) + "x")

    print_violations(f"units_sold <= {MAX_MULTIPLE_OF_MEDIAN}x the slot's median daily units", report)
    spike_count = len(spikes)
    assert spike_count == 0, (
        f"{spike_count} slot-day(s) above {MAX_MULTIPLE_OF_MEDIAN}x median units "
        f"on {spikes['planogram_id'].nunique()} slot(s)"
    )


def test_waste_only_on_perishable_skus(tables):
    sales = tables["daily_sku_sales"]
    # One row per planogram_id so this check cannot itself fan out on a
    # duplicated planogram row; test_primary_keys.py reports duplicates.
    slots = tables["planogram"].drop_duplicates("planogram_id")[["planogram_id", "sku"]]
    skus = tables["sku_master"].drop_duplicates("sku")[["sku", "category", "is_perishable"]]
    lookup = slots.merge(skus, on="sku", how="left").set_index("planogram_id")

    waste = pd.to_numeric(sales["waste_units"], errors="coerce")
    perishable = sales["planogram_id"].map(lookup["is_perishable"])
    # Rows whose SKU cannot be resolved are left to the referential-integrity tests.
    bad = sales[(waste > 0) & (perishable == "False")].assign(
        sku=lambda d: d["planogram_id"].map(lookup["sku"]),
        category=lambda d: d["planogram_id"].map(lookup["category"]),
    )

    print_violations("waste_units > 0 only on perishable SKUs", bad)
    assert bad.empty, f"{len(bad)} row(s) record perishable waste on a shelf-stable SKU"
