"""Build the category managers' daily units-by-SKU-by-store table.

    python scripts/build_category_mart.py [--data-dir DIR] [--out FILE]

Defaults: --data-dir data/synthetic, --out build/mart/category_daily_units.csv.

Joins daily_sku_sales to planogram, store_reference and sku_master and writes
category_daily_units.csv: one row per store, SKU and business date, with the
store and item attributes category managers filter on, the promotion flag,
and promo lift against the slot's non-promotion baseline.

Run it only after the quality gate passes. It re-checks the one property the
published numbers depend on most, one output row per sales row, and refuses to
write the file if any join fans out or drops rows.

Exit status: 0 written, 1 the inputs would produce a wrong table (nothing written).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = REPO_ROOT / "data" / "synthetic"
DEFAULT_OUT = REPO_ROOT / "build" / "mart" / "category_daily_units.csv"

STORE_COLUMNS = ["store_code", "store_name", "region", "store_format"]
SKU_COLUMNS = ["sku", "item_description", "department", "category", "is_perishable"]
OUTPUT_COLUMNS = [
    "business_date", "store_code", "store_name", "region", "store_format",
    "sku", "item_description", "department", "category", "is_perishable",
    "units_sold", "net_sales", "basket_count", "promo_flag", "promo_id",
    "baseline_units", "promo_lift_pct", "shrink_units", "waste_units",
]
PRIMARY_KEY = ["business_date", "store_code", "sku"]


class MartBuildError(Exception):
    """The inputs would produce a table that overstates, understates or duplicates sales."""


def read(data_dir: Path, name: str) -> pd.DataFrame:
    return pd.read_csv(data_dir / f"{name}.csv", dtype=str, keep_default_na=False, na_values=[""])


def build(sales: pd.DataFrame, planogram: pd.DataFrame, stores: pd.DataFrame, skus: pd.DataFrame) -> pd.DataFrame:
    """Return the mart. Every input column arrives as text, as read from CSV."""
    sales = sales.astype({"units_sold": "int64", "basket_count": "int64", "shrink_units": "int64",
                          "waste_units": "int64", "net_sales": "float64"})
    sales["promo_flag"] = sales["promo_flag"] == "True"

    out = sales
    for right, key, columns, name in [
        (planogram, "planogram_id", ["planogram_id", "store_code", "sku"], "planogram"),
        (stores, "store_code", STORE_COLUMNS, "store_reference"),
        (skus, "sku", SKU_COLUMNS, "sku_master"),
    ]:
        if not right[key].is_unique:
            dupes = sorted(right.loc[right[key].duplicated(keep=False), key].dropna().unique())
            raise MartBuildError(f"{name}.{key} is not unique ({', '.join(dupes)}); the join would "
                                 f"fan out and multiply those rows' units")
        before = len(out)
        out = out.merge(right[columns], on=key, how="inner", validate="many_to_one")
        if len(out) != before:
            raise MartBuildError(f"joining {name} on {key} dropped {before - len(out)} sales row(s) "
                                 f"with no matching {name} row")

    if out.duplicated(PRIMARY_KEY).any():
        raise MartBuildError(f"{int(out.duplicated(PRIMARY_KEY).sum())} duplicate "
                             f"({', '.join(PRIMARY_KEY)}) row(s): two planogram slots for one store and SKU")

    # Promo lift: units on a promotion day against the slot's average units on
    # non-promotion days in the same window. Undefined when the slot has no
    # non-promotion sales to compare against.
    baseline = out.loc[~out["promo_flag"]].groupby("planogram_id")["units_sold"].mean()
    out["baseline_units"] = out["planogram_id"].map(baseline).round(2)
    lift = out["units_sold"] / out["baseline_units"] - 1
    out["promo_lift_pct"] = (lift * 100).round(1).where(out["promo_flag"] & (out["baseline_units"] > 0))

    return out[OUTPUT_COLUMNS].sort_values(PRIMARY_KEY, ignore_index=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)
    out_path = args.out

    try:
        mart = build(read(args.data_dir, "daily_sku_sales"), read(args.data_dir, "planogram"),
                     read(args.data_dir, "store_reference"), read(args.data_dir, "sku_master"))
    except MartBuildError as err:
        print(f"MART NOT BUILT: {err}", file=sys.stderr)
        return 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    mart.to_csv(out_path, index=False, lineterminator="\n")
    promo = mart[mart["promo_flag"]]
    print(f"wrote {out_path}: {len(mart)} rows, {mart['store_code'].nunique()} stores, "
          f"{mart['sku'].nunique()} SKUs, {mart['business_date'].min()} to {mart['business_date'].max()}; "
          f"{len(promo)} promo rows, median promo lift {promo['promo_lift_pct'].median():.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
