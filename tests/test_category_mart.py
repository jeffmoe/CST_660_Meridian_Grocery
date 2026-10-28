"""Unit tests for the published mart builder (scripts/build_category_mart.py).

These run on a small hand-built dataset rather than --data-dir, so they test
the builder's logic and guards and give the same result on any dataset. The
data-quality tests elsewhere in this folder cover the delivered data itself.
"""

from __future__ import annotations

import pandas as pd
import pytest

from scripts.build_category_mart import OUTPUT_COLUMNS, PRIMARY_KEY, MartBuildError, build


def frame(rows: list[dict]) -> pd.DataFrame:
    """Every value as text, as the builder reads it from CSV."""
    return pd.DataFrame(rows, dtype=object)


def sale(planogram_id, date, units, promo_id=None):
    return {"planogram_id": planogram_id, "business_date": date, "units_sold": str(units),
            "net_sales": f"{units * 2.5:.2f}", "basket_count": str(max(units - 1, 0)),
            "promo_flag": "True" if promo_id else "False", "promo_id": promo_id,
            "shrink_units": "0", "waste_units": "0"}


@pytest.fixture
def inputs() -> dict[str, pd.DataFrame]:
    stores = frame([
        {"store_code": "ST001", "store_name": "Meridian Ames", "region": "Central", "store_format": "SUPERMARKET"},
        {"store_code": "ST002", "store_name": "Meridian Omaha", "region": "West", "store_format": "EXPRESS"},
    ])
    skus = frame([
        {"sku": "100001", "item_description": "Harvest Lane Bananas", "department": "PRODUCE",
         "category": "FRESH_FRUIT", "is_perishable": "True"},
        {"sku": "100002", "item_description": "Golden Acre Pretzels", "department": "GROCERY",
         "category": "SNACKS", "is_perishable": "False"},
    ])
    planogram = frame([
        {"planogram_id": "PG00000001", "store_code": "ST001", "sku": "100001"},
        {"planogram_id": "PG00000002", "store_code": "ST002", "sku": "100002"},
    ])
    sales = frame([
        sale("PG00000001", "2026-10-01", 10),
        sale("PG00000001", "2026-10-02", 14),
        sale("PG00000001", "2026-10-03", 30, promo_id="PR000001"),
        sale("PG00000002", "2026-10-01", 0),
        sale("PG00000002", "2026-10-02", 0),
        sale("PG00000002", "2026-10-03", 5, promo_id="PR000002"),
    ])
    return {"sales": sales, "planogram": planogram, "stores": stores, "skus": skus}


def test_one_row_per_sales_row_with_attributes(inputs):
    mart = build(**inputs)
    assert list(mart.columns) == OUTPUT_COLUMNS
    assert len(mart) == len(inputs["sales"])
    assert not mart.duplicated(PRIMARY_KEY).any()
    row = mart[(mart["store_code"] == "ST001") & (mart["business_date"] == "2026-10-01")].iloc[0]
    assert (row["sku"], row["item_description"], row["region"]) == ("100001", "Harvest Lane Bananas", "Central")
    assert mart["units_sold"].sum() == inputs["sales"]["units_sold"].astype(int).sum()


def test_promo_lift_against_non_promo_baseline(inputs):
    mart = build(**inputs).set_index(["store_code", "business_date"])
    # Baseline is the mean of the non-promo days: (10 + 14) / 2 = 12; 30 units is +150%.
    assert mart.loc[("ST001", "2026-10-03"), "baseline_units"] == 12.0
    assert mart.loc[("ST001", "2026-10-03"), "promo_lift_pct"] == 150.0
    # Lift is only reported on promotion days.
    assert mart.loc[[("ST001", "2026-10-01"), ("ST001", "2026-10-02")], "promo_lift_pct"].isna().all()
    # A zero baseline has no meaningful lift.
    assert mart.loc[("ST002", "2026-10-03"), "baseline_units"] == 0
    assert pd.isna(mart.loc[("ST002", "2026-10-03"), "promo_lift_pct"])


def test_duplicate_store_key_is_refused(inputs):
    stores = inputs["stores"]
    inputs["stores"] = pd.concat([stores, stores.iloc[[0]].assign(region="North")], ignore_index=True)
    with pytest.raises(MartBuildError, match=r"store_reference\.store_code is not unique \(ST001\)"):
        build(**inputs)


def test_duplicate_planogram_key_is_refused(inputs):
    planogram = inputs["planogram"]
    inputs["planogram"] = pd.concat([planogram, planogram.iloc[[1]]], ignore_index=True)
    with pytest.raises(MartBuildError, match=r"planogram\.planogram_id is not unique \(PG00000002\)"):
        build(**inputs)


def test_unmatched_dimension_row_is_refused(inputs):
    inputs["planogram"].loc[1, "store_code"] = "ST099"
    with pytest.raises(MartBuildError, match=r"joining store_reference on store_code dropped 3 sales row"):
        build(**inputs)


def test_two_slots_for_one_store_and_sku_are_refused(inputs):
    extra_slot = {"planogram_id": "PG00000003", "store_code": "ST001", "sku": "100001"}
    inputs["planogram"] = pd.concat([inputs["planogram"], frame([extra_slot])], ignore_index=True)
    inputs["sales"] = pd.concat([inputs["sales"], frame([sale("PG00000003", "2026-10-01", 4)])],
                                ignore_index=True)
    with pytest.raises(MartBuildError, match="two planogram slots for one store and SKU"):
        build(**inputs)
