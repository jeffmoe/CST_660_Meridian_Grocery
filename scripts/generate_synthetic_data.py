"""Generate a synthetic Meridian Grocery Co-op store-sales dataset.

Writes four CSVs (store_reference, sku_master, planogram, daily_sku_sales) to
data/synthetic/ plus a manifest describing every deliberately injected defect.
Output is fully deterministic for a given --seed (with the pinned versions in
requirements.txt), so downstream tests can assert against exact violation counts.

Injected defects (the failure classes the quality gates must catch):
  * duplicate planogram_id rows in planogram
  * planogram slots whose store_code has no match in store_reference (orphans)
  * planogram slots with a null sku
  * one store_reference row that duplicates an existing store_code, so any
    planogram -> store_reference join fans out for that store
  * one daily units_sold value with a decimal-shift error (x100 for a single day)

Run with:  python scripts/generate_synthetic_data.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "data" / "synthetic"

# Six full ad weeks. The co-op's weekly ad circular runs Wednesday to Tuesday,
# so the window starts on a Wednesday and ends on the Tuesday before the
# 2026-10-28 07:00 refresh.
AS_OF = pd.Timestamp("2026-10-27")
N_DAYS = 42
WINDOW_START = AS_OF - pd.Timedelta(days=N_DAYS - 1)
DATES = pd.date_range(WINDOW_START, AS_OF, freq="D")

# (city, state, region, format) -- 40 stores across the Midwest footprint.
STORES = [
    ("Des Moines", "IA", "Central", "SUPERMARKET"), ("Ames", "IA", "Central", "NEIGHBORHOOD"),
    ("Ankeny", "IA", "Central", "SUPERMARKET"), ("West Des Moines", "IA", "Central", "SUPERMARKET"),
    ("Cedar Rapids", "IA", "East", "SUPERMARKET"), ("Iowa City", "IA", "East", "NEIGHBORHOOD"),
    ("Davenport", "IA", "East", "SUPERMARKET"), ("Dubuque", "IA", "East", "NEIGHBORHOOD"),
    ("Waterloo", "IA", "North", "NEIGHBORHOOD"), ("Mason City", "IA", "North", "EXPRESS"),
    ("Sioux City", "IA", "West", "NEIGHBORHOOD"), ("Council Bluffs", "IA", "West", "NEIGHBORHOOD"),
    ("Omaha", "NE", "West", "SUPERMARKET"), ("Lincoln", "NE", "West", "SUPERMARKET"),
    ("Grand Island", "NE", "West", "EXPRESS"), ("Kearney", "NE", "West", "EXPRESS"),
    ("Minneapolis", "MN", "North", "SUPERMARKET"), ("St. Paul", "MN", "North", "SUPERMARKET"),
    ("Rochester", "MN", "North", "NEIGHBORHOOD"), ("Mankato", "MN", "North", "EXPRESS"),
    ("St. Cloud", "MN", "North", "NEIGHBORHOOD"), ("Madison", "WI", "East", "SUPERMARKET"),
    ("La Crosse", "WI", "East", "NEIGHBORHOOD"), ("Eau Claire", "WI", "North", "NEIGHBORHOOD"),
    ("Green Bay", "WI", "East", "SUPERMARKET"), ("Rockford", "IL", "East", "SUPERMARKET"),
    ("Peoria", "IL", "East", "NEIGHBORHOOD"), ("Moline", "IL", "East", "EXPRESS"),
    ("Springfield", "IL", "South", "SUPERMARKET"), ("Champaign", "IL", "South", "NEIGHBORHOOD"),
    ("Kansas City", "MO", "South", "SUPERMARKET"), ("Columbia", "MO", "South", "NEIGHBORHOOD"),
    ("St. Joseph", "MO", "South", "EXPRESS"), ("Springfield", "MO", "South", "SUPERMARKET"),
    ("Topeka", "KS", "South", "NEIGHBORHOOD"), ("Lawrence", "KS", "South", "NEIGHBORHOOD"),
    ("Sioux Falls", "SD", "West", "SUPERMARKET"), ("Fargo", "ND", "North", "SUPERMARKET"),
    ("Bismarck", "ND", "North", "NEIGHBORHOOD"), ("Grand Forks", "ND", "North", "EXPRESS"),
]
assert len(STORES) == 40

# store_format -> (share of the assortment carried, traffic multiplier)
FORMATS = {
    "SUPERMARKET": (0.75, 1.00),
    "NEIGHBORHOOD": (0.60, 0.70),
    "EXPRESS": (0.40, 0.35),
}

# category -> (department, perishable, shelf-life days (lo, hi), shelf location,
#              price (lo, hi), median units per day in a supermarket, daily waste rate)
CATEGORIES = {
    "FRESH_FRUIT":       ("PRODUCE", True, (4, 14), "PRODUCE_TABLE", (0.59, 4.99), 14.0, 0.06),
    "FRESH_VEGETABLES":  ("PRODUCE", True, (5, 21), "PRODUCE_TABLE", (0.99, 4.49), 10.0, 0.05),
    "MILK":              ("DAIRY", True, (10, 18), "COOLER", (2.49, 4.99), 16.0, 0.02),
    "YOGURT_CHEESE":     ("DAIRY", True, (21, 60), "COOLER", (2.29, 6.49), 6.0, 0.02),
    "EGGS_BUTTER":       ("DAIRY", True, (28, 45), "COOLER", (2.99, 6.99), 9.0, 0.01),
    "FRESH_MEAT":        ("MEAT_SEAFOOD", True, (3, 7), "COOLER", (3.99, 9.99), 6.0, 0.07),
    "SEAFOOD":           ("MEAT_SEAFOOD", True, (2, 5), "COOLER", (6.99, 12.99), 2.5, 0.08),
    "BREAD":             ("BAKERY", True, (5, 9), "BAKERY_CASE", (2.29, 5.49), 8.0, 0.05),
    "SWEET_GOODS":       ("BAKERY", True, (3, 6), "BAKERY_CASE", (3.49, 6.99), 3.0, 0.08),
    "CEREAL_BREAKFAST":  ("GROCERY", False, None, "AISLE", (2.49, 5.99), 4.0, 0.0),
    "PASTA_SAUCE":       ("GROCERY", False, None, "AISLE", (1.29, 4.49), 4.5, 0.0),
    "CANNED_GOODS":      ("GROCERY", False, None, "AISLE", (0.89, 2.99), 5.0, 0.0),
    "SNACKS":            ("GROCERY", False, None, "AISLE", (2.49, 5.49), 5.5, 0.0),
    "BEVERAGES":         ("GROCERY", False, None, "AISLE", (3.49, 8.99), 6.0, 0.0),
    "FROZEN_MEALS":      ("FROZEN", False, None, "FREEZER", (4.49, 9.99), 3.5, 0.0),
    "FROZEN_DESSERTS":   ("FROZEN", False, None, "FREEZER", (3.99, 6.99), 3.0, 0.0),
    "FROZEN_VEGETABLES": ("FROZEN", False, None, "FREEZER", (1.29, 2.99), 2.5, 0.0),
    "PAPER_CLEANING":    ("HOUSEHOLD", False, None, "AISLE", (3.99, 14.99), 3.0, 0.0),
}

ITEMS = {
    "FRESH_FRUIT": ["Bananas", "Gala Apples", "Strawberries 1 lb", "Blueberries 6 oz",
                    "Navel Oranges", "Red Seedless Grapes", "Hass Avocados", "Lemons"],
    "FRESH_VEGETABLES": ["Romaine Hearts 3 ct", "Baby Carrots 1 lb", "Roma Tomatoes", "Yellow Onions 3 lb",
                         "Russet Potatoes 5 lb", "Broccoli Crowns", "Spring Salad Mix 5 oz", "Cucumbers"],
    "MILK": ["2% Milk Gallon", "Whole Milk Gallon", "Skim Milk Half Gallon", "Oat Milk 64 oz"],
    "YOGURT_CHEESE": ["Plain Greek Yogurt 32 oz", "Sharp Cheddar 8 oz", "Shredded Mozzarella 16 oz",
                      "String Cheese 12 ct", "Cottage Cheese 16 oz"],
    "EGGS_BUTTER": ["Large Eggs 12 ct", "Large Eggs 18 ct", "Salted Butter 1 lb"],
    "FRESH_MEAT": ["80/20 Ground Beef", "Boneless Chicken Breast", "Pork Loin Chops", "Thick Cut Bacon 12 oz"],
    "SEAFOOD": ["Atlantic Salmon Fillet", "Tilapia Fillet", "Cooked Shrimp 12 oz"],
    "BREAD": ["White Sandwich Bread", "Whole Wheat Bread", "Plain Bagels 6 ct",
              "Hamburger Buns 8 ct", "Sourdough Loaf"],
    "SWEET_GOODS": ["Blueberry Muffins 4 ct", "Glazed Donuts 6 ct", "Chocolate Chip Cookies 12 ct"],
    "CEREAL_BREAKFAST": ["Honey Oat Cereal", "Corn Flakes", "Old Fashioned Oats 42 oz", "Buttermilk Pancake Mix"],
    "PASTA_SAUCE": ["Spaghetti 16 oz", "Penne Rigate 16 oz", "Marinara Sauce 24 oz", "Alfredo Sauce 15 oz"],
    "CANNED_GOODS": ["Chicken Noodle Soup", "Black Beans 15 oz", "Diced Tomatoes 14.5 oz",
                     "Sweet Corn 15 oz", "Chunk Light Tuna 5 oz"],
    "SNACKS": ["Potato Chips 8 oz", "Tortilla Chips 13 oz", "Pretzel Twists 16 oz",
               "Microwave Popcorn 3 ct", "Creamy Peanut Butter 16 oz"],
    "BEVERAGES": ["Ground Coffee 12 oz", "Sparkling Water 12 pk", "Cola 12 pk", "Apple Juice 64 oz"],
    "FROZEN_MEALS": ["Pepperoni Pizza", "Four Cheese Pizza", "Chicken Nuggets 2 lb", "Meat Lasagna"],
    "FROZEN_DESSERTS": ["Vanilla Ice Cream 48 oz", "Chocolate Ice Cream 48 oz", "Fruit Bars 6 ct"],
    "FROZEN_VEGETABLES": ["Sweet Peas 12 oz", "Broccoli Florets 12 oz", "Mixed Vegetables 12 oz"],
    "PAPER_CLEANING": ["Paper Towels 6 roll", "Bath Tissue 12 roll", "Dish Soap 24 oz",
                       "Laundry Detergent 92 oz", "Trash Bags 40 ct"],
}

# Items sold by weight; every other item is sold each.
SOLD_BY_POUND = {"Bananas", "Gala Apples", "Roma Tomatoes", "80/20 Ground Beef",
                 "Boneless Chicken Breast", "Pork Loin Chops", "Atlantic Salmon Fillet", "Tilapia Fillet"}

PRIVATE_LABEL = "Meridian Select"
NATIONAL_BRANDS = {"PRODUCE": "Harvest Lane", "DAIRY": "Northfield", "MEAT_SEAFOOD": "Cedar Creek",
                   "BAKERY": "Prairie Hearth", "GROCERY": "Golden Acre", "FROZEN": "Lakeshore",
                   "HOUSEHOLD": "Brightway"}

# Day-of-week traffic, Monday first.
DOW_FACTOR = np.array([0.85, 0.85, 0.95, 1.00, 1.10, 1.25, 1.15])
ENDCAP_LIFT = 1.3
PROMO_SHARE = 0.12          # share of SKUs on ad each week
REGIONAL_PROMO_SHARE = 0.3  # of those, share run in one region only
REGIONS = ["Central", "East", "North", "South", "West"]


def store_codes(idx) -> np.ndarray:
    """1-based store numbers -> 'ST001' style codes."""
    return np.char.add("ST", np.char.zfill(np.asarray(idx).astype(str), 3))


def random_dates(rng: np.random.Generator, start, end, size: int) -> pd.DatetimeIndex:
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    offsets = rng.integers(0, (end - start).days + 1, size=size)
    return start + pd.to_timedelta(offsets, unit="D")


def build_stores(rng: np.random.Generator) -> pd.DataFrame:
    df = pd.DataFrame(STORES, columns=["city", "state", "region", "store_format"])
    df.insert(0, "store_code", store_codes(np.arange(1, len(df) + 1)))
    # Two Springfields: disambiguate by state.
    suffix = np.where(df["city"] == "Springfield", " " + df["state"], "")
    df.insert(1, "store_name", "Meridian " + df["city"] + suffix)
    df["opened_date"] = random_dates(rng, "1978-01-01", "2023-12-31", len(df))
    df["is_active"] = True
    return df


def build_skus(rng: np.random.Generator) -> pd.DataFrame:
    """Every item is stocked under a national brand and the co-op's private label."""
    rows = []
    for category, items in ITEMS.items():
        dept, perishable, life, _, (p_lo, p_hi), _, _ = CATEGORIES[category]
        for item in items:
            for brand in (NATIONAL_BRANDS[dept], PRIVATE_LABEL):
                rows.append({"brand": brand, "department": dept, "category": category, "item": item,
                             "unit_of_measure": "LB" if item in SOLD_BY_POUND else "EA",
                             "is_perishable": perishable, "life": life, "price_range": (p_lo, p_hi)})
    df = pd.DataFrame(rows)
    n = len(df)
    life_lo = df["life"].map(lambda r: r[0] if r else 0).to_numpy()
    life_hi = df["life"].map(lambda r: r[1] if r else 0).to_numpy()
    shelf_life = pd.array(rng.integers(life_lo, life_hi + 1), dtype="Int64")
    shelf_life[~df["is_perishable"].to_numpy()] = pd.NA

    return pd.DataFrame({
        "sku": [str(100001 + i) for i in range(n)],
        "item_description": df["brand"] + " " + df["item"],
        "brand": df["brand"],
        "department": df["department"],
        "category": df["category"],
        "unit_of_measure": df["unit_of_measure"],
        "is_perishable": df["is_perishable"],
        "shelf_life_days": shelf_life,
        "launch_date": random_dates(rng, "2008-01-01", "2026-06-30", n),
    })


def build_planogram(rng: np.random.Generator, stores: pd.DataFrame, skus: pd.DataFrame) -> pd.DataFrame:
    """One slot per (store, SKU) the store carries."""
    carry = stores["store_format"].map(lambda f: FORMATS[f][0]).to_numpy()
    carried = rng.random((len(stores), len(skus))) < carry[:, None]
    s_idx, k_idx = np.nonzero(carried)
    n = len(s_idx)
    store = stores.iloc[s_idx].reset_index(drop=True)
    sku = skus.iloc[k_idx].reset_index(drop=True)

    # Slots are set after both the store opened and the item launched; ~3% are
    # new items cut in during the window.
    earliest = np.maximum(store["opened_date"].to_numpy(), sku["launch_date"].to_numpy())
    span = np.maximum((WINDOW_START - pd.DatetimeIndex(earliest)).days.to_numpy(), 1)
    set_date = pd.DatetimeIndex(earliest) + pd.to_timedelta(rng.integers(0, span), unit="D")
    cut_in = rng.random(n) < 0.03
    set_date = set_date.where(~cut_in, WINDOW_START + pd.to_timedelta(rng.integers(1, N_DAYS - 4, size=n), unit="D"))

    # ~2% are discontinued (pulled from the shelf) during the window.
    removal_candidate = np.maximum(set_date, WINDOW_START) + pd.to_timedelta(rng.integers(5, 36, size=n), unit="D")
    removed = (rng.random(n) < 0.02) & (removal_candidate <= AS_OF)
    removal_date = pd.Series(removal_candidate).where(removed)

    # Most slots sit in the category's home location; ~6% are on an endcap.
    home = sku["category"].map(lambda c: CATEGORIES[c][3]).to_numpy()
    location = np.where(rng.random(n) < 0.06, "ENDCAP", home)

    # Regular shelf price: a SKU list price within its category band, times a
    # small per-store price-zone factor.
    p_lo = sku["category"].map(lambda c: CATEGORIES[c][4][0]).to_numpy()
    p_hi = sku["category"].map(lambda c: CATEGORIES[c][4][1]).to_numpy()
    sku_pos = rng.random(len(skus))[k_idx]
    zone = rng.uniform(0.97, 1.05, size=len(stores))[s_idx]
    price = np.round((p_lo + sku_pos * (p_hi - p_lo)) * zone, 2)

    return pd.DataFrame({
        "planogram_id": [f"PG{i:08d}" for i in range(1, n + 1)],
        "store_code": store["store_code"],
        "sku": sku["sku"],
        "shelf_location": location,
        "set_date": set_date,
        "removal_date": removal_date,
        "status": np.where(removed, "REMOVED", "ACTIVE"),
        "regular_price": price,
    })


def build_promo_calendar(rng: np.random.Generator, skus: pd.DataFrame) -> pd.DataFrame:
    """Weekly ad events: (ad_week, sku) -> promo_id, discount, lift, region scope."""
    rows, next_id = [], 1
    n_weeks = N_DAYS // 7
    for week in range(n_weeks):
        on_ad = rng.choice(len(skus), size=round(PROMO_SHARE * len(skus)), replace=False)
        for k in sorted(on_ad):
            discount = float(rng.uniform(0.10, 0.35))
            regional = rng.random() < REGIONAL_PROMO_SHARE
            rows.append({
                "ad_week": week,
                "sku": skus["sku"].iloc[k],
                "promo_id": f"PR{next_id:06d}",
                "discount": discount,
                # Deeper discounts drive more volume: 1.6x-2.6x before noise.
                "lift": 1.25 + 4 * discount + rng.normal(0, 0.1),
                "region": rng.choice(REGIONS) if regional else None,
            })
            next_id += 1
    return pd.DataFrame(rows)


def build_daily_sales(rng: np.random.Generator, stores: pd.DataFrame, skus: pd.DataFrame,
                      planogram: pd.DataFrame, promos: pd.DataFrame) -> pd.DataFrame:
    """Simulate a (slots x days) sales matrix, then flatten to one row per open slot-day."""
    n, t = len(planogram), len(DATES)
    store = stores.set_index("store_code").loc[planogram["store_code"]]
    sku = skus.set_index("sku").loc[planogram["sku"]]
    category = sku["category"].to_numpy()

    # Expected units per day: category velocity x SKU appeal x store traffic.
    cat_rate = np.array([CATEGORIES[c][5] for c in category])
    sku_appeal = pd.Series(rng.lognormal(0, 0.45, size=len(skus)), index=skus["sku"]).loc[planogram["sku"]].to_numpy()
    traffic = store["store_format"].map(lambda f: FORMATS[f][1]).to_numpy()
    store_noise = pd.Series(rng.lognormal(0, 0.2, size=len(stores)), index=stores["store_code"]) \
        .loc[planogram["store_code"]].to_numpy()
    base = cat_rate * sku_appeal * traffic * store_noise
    base = np.where(planogram["shelf_location"].to_numpy() == "ENDCAP", base * ENDCAP_LIFT, base)
    rate = base[:, None] * DOW_FACTOR[DATES.dayofweek.to_numpy()][None, :]

    # Promotions: an ad event covers every day of its ad week in every store in scope.
    ad_week = (np.arange(t) // 7)
    promo_id = np.full((n, t), None, dtype=object)
    on_promo = np.zeros((n, t), dtype=bool)
    discount = np.zeros((n, t))
    region = store["region"].to_numpy()
    slot_sku = planogram["sku"].to_numpy()
    for p in promos.itertuples():
        slots = slot_sku == p.sku
        if pd.notna(p.region):
            slots &= region == p.region
        days = ad_week == p.ad_week
        mask = slots[:, None] & days[None, :]
        promo_id[mask] = p.promo_id
        on_promo |= mask
        discount[mask] = p.discount
        rate[mask] *= p.lift

    units = rng.poisson(rate)
    # A basket holds one or more of a SKU: basket_count is between 1 and units.
    baskets = np.where(units > 0, 1 + rng.binomial(np.maximum(units - 1, 0), 0.8), 0)

    price = planogram["regular_price"].to_numpy()[:, None]
    net_sales = np.round(units * price * (1 - discount), 2)

    # Shrink: unexplained loss (theft, damage, scan errors) on every item.
    # Perishable waste: product pulled past its sell-by date, perishables only.
    shrink = rng.poisson(rate * 0.01)
    waste_rate = np.array([CATEGORIES[c][6] for c in category])[:, None]
    waste = rng.poisson(rate * waste_rate)

    # Only emit rows for days the slot was on the shelf.
    first = planogram["set_date"].clip(lower=WINDOW_START).to_numpy()[:, None]
    last = (planogram["removal_date"] - pd.Timedelta(days=1)).fillna(AS_OF).to_numpy()[:, None]
    dates = DATES.to_numpy()[None, :]
    on_shelf = (dates >= first) & (dates <= last)

    rows, cols = np.nonzero(on_shelf)
    return pd.DataFrame({
        "planogram_id": planogram["planogram_id"].to_numpy()[rows],
        "business_date": DATES[cols],
        "units_sold": units[rows, cols],
        "net_sales": net_sales[rows, cols],
        "basket_count": baskets[rows, cols],
        "promo_flag": on_promo[rows, cols],
        "promo_id": promo_id[rows, cols],
        "shrink_units": shrink[rows, cols],
        "waste_units": waste[rows, cols],
    })


def inject_defects(rng: np.random.Generator, stores: pd.DataFrame, planogram: pd.DataFrame,
                   sales: pd.DataFrame):
    """Return broken copies of stores/planogram/sales and a manifest of what was broken."""
    manifest: dict = {}
    planogram = planogram.copy()
    sales = sales.copy()

    # 1. Fan-out key: a district re-org inserted a new row for ST017 without
    #    retiring the old one, so store_code is no longer unique.
    fanout_code = "ST017"
    pos = stores.index[stores["store_code"] == fanout_code][0]
    dup = stores.loc[[pos]].assign(region="Central", store_name=lambda d: d["store_name"] + " (Uptown)")
    stores = pd.concat([stores.loc[:pos], dup, stores.loc[pos + 1:]], ignore_index=True)
    manifest["store_reference_duplicate_key"] = {
        "store_code": fanout_code,
        "row_count_for_key": 2,
        "description": "Second store_reference row for ST017 (district re-org) "
                       "without retiring the original; joins on store_code double this store's slots.",
    }

    # Distinct victims for each remaining defect so counts stay exact.
    victims = rng.choice(len(planogram), size=12, replace=False)

    # 2. Orphan store codes: codes that never existed, or a closed store that
    #    was purged from the reference table.
    orphan_idx = victims[0:5]
    planogram.loc[orphan_idx, "store_code"] = ["ST041", "ST099", "ST000", "ST041", "ST7"]
    manifest["orphan_store_codes"] = {
        "count": len(orphan_idx),
        "rows": planogram.loc[orphan_idx, ["planogram_id", "store_code"]].to_dict("records"),
    }

    # 3. Null SKUs: slots exported before the item was keyed in the item file.
    null_idx = victims[5:9]
    planogram.loc[null_idx, "sku"] = None
    manifest["null_skus"] = {
        "count": len(null_idx),
        "planogram_ids": planogram.loc[null_idx, "planogram_id"].tolist(),
    }

    # 4. Duplicate planogram IDs: one exact double-load and two conflicting rows
    #    (same ID, different store and shelf location), placed next to the source row.
    exact = planogram.loc[[victims[9]]]
    conflicts = planogram.loc[victims[10:12]].copy()
    conflicts["store_code"] = store_codes(rng.integers(1, len(STORES) + 1, size=len(conflicts)))
    locations = sorted({c[3] for c in CATEGORIES.values()} | {"ENDCAP"})
    conflicts["shelf_location"] = [rng.choice([loc for loc in locations if loc != cur])
                                   for cur in conflicts["shelf_location"]]
    extras = pd.concat([exact, conflicts])
    planogram = (
        pd.concat([planogram, extras])
        .sort_index(kind="stable")  # duplicates land directly after their source row
        .reset_index(drop=True)
    )
    manifest["duplicate_planogram_ids"] = {
        "distinct_ids_duplicated": len(extras),
        "extra_rows": len(extras),
        "rows": [{"planogram_id": p, "kind": "exact_duplicate_row"} for p in exact["planogram_id"]]
              + [{"planogram_id": p, "kind": "conflicting_attributes"} for p in conflicts["planogram_id"]],
    }

    # Counted after every mutation so it reflects the final planogram table.
    manifest["store_reference_duplicate_key"]["planogram_rows_affected_by_fanout"] = int(
        (planogram["store_code"] == fanout_code).sum())

    # 5. Decimal-shift spike: one day's units_sold posted x100 (the POS quantity
    #    field read with two implied decimals; net_sales came through correctly).
    #    The victim is an otherwise clean slot on the shelf for the whole window,
    #    so this is the only defect that touches it, and the day is a
    #    non-promotion day with at least 25 units so the posted value also
    #    breaks the contract's units ceiling.
    clean = planogram[
        ~planogram["planogram_id"].duplicated(keep=False)
        & planogram["sku"].notna()
        & planogram["store_code"].isin(stores["store_code"])
        & (planogram["store_code"] != fanout_code)
        & (planogram["set_date"] < WINDOW_START)
        & planogram["removal_date"].isna()
    ]
    spike_date = DATES[rng.integers(10, N_DAYS - 10)]
    candidates = sales[(sales["business_date"] == spike_date)
                       & sales["planogram_id"].isin(clean["planogram_id"])
                       & ~sales["promo_flag"]
                       & (sales["units_sold"] >= 25)]
    hit_index = candidates.index[rng.integers(len(candidates))]
    before = int(sales.loc[hit_index, "units_sold"])
    sales.loc[hit_index, "units_sold"] = before * 100
    manifest["units_decimal_shift"] = {
        "count": 1,
        "planogram_id": sales.loc[hit_index, "planogram_id"],
        "business_date": spike_date.date().isoformat(),
        "units_sold_correct": before,
        "units_sold_posted": before * 100,
    }

    return stores, planogram, sales, manifest


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seed", type=int, default=660)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--clean", action="store_true", help="skip defect injection")
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)

    stores = build_stores(rng)
    skus = build_skus(rng)
    planogram = build_planogram(rng, stores, skus)
    promos = build_promo_calendar(rng, skus)
    # Sales are generated from the clean planogram: one series per real slot.
    sales = build_daily_sales(rng, stores, skus, planogram, promos)

    manifest = {}
    if not args.clean:
        stores, planogram, sales, manifest = inject_defects(rng, stores, planogram, sales)

    args.out.mkdir(parents=True, exist_ok=True)
    csv_opts = {"index": False, "date_format": "%Y-%m-%d", "lineterminator": "\n"}
    stores.to_csv(args.out / "store_reference.csv", **csv_opts)
    skus.to_csv(args.out / "sku_master.csv", **csv_opts)
    planogram.to_csv(args.out / "planogram.csv", float_format="%.2f", **csv_opts)
    sales.to_csv(args.out / "daily_sku_sales.csv", float_format="%.2f", **csv_opts)

    full_manifest = {
        "seed": args.seed,
        "as_of_date": AS_OF.date().isoformat(),
        "window_start": WINDOW_START.date().isoformat(),
        "days": N_DAYS,
        "row_counts": {
            "store_reference": len(stores),
            "sku_master": len(skus),
            "planogram": len(planogram),
            "daily_sku_sales": len(sales),
        },
        "promo_events": len(promos),
        "injected_defects": manifest,
    }
    (args.out / "manifest.json").write_text(json.dumps(full_manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(full_manifest["row_counts"], indent=2))


if __name__ == "__main__":
    main()
