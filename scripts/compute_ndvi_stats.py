#!/usr/bin/env python3
"""
Compute per-date and yearly mean NDVI per Taiwan county from extracted GeoTIFFs.

Reads data/tiff/*.tif (already reprojected, mosaicked, QA-filtered)
and outputs data/county_ndvi.json.

If GeoTIFFs don't exist yet, run extract_ndvi_tiff.py first.
"""

import json
import glob
import warnings
from pathlib import Path

import numpy as np
import geopandas as gpd
import rasterio
from rasterio.mask import mask as rasterio_mask
from shapely.geometry import mapping

warnings.filterwarnings("ignore")

PROJECT_DIR = Path(__file__).parent.parent
DATA_DIR = PROJECT_DIR / "data"
TIFF_DIR = DATA_DIR / "tiff"
COUNTY_GEOJSON = Path("/home/kiang/public_html/taiwan_basecode/county/geo/20200820.json")
OUTPUT_JSON = DATA_DIR / "county_ndvi.json"

NDVI_SCALE = 0.0001
DST_CRS = "EPSG:4326"


def find_tiff_files():
    pattern = str(TIFF_DIR / "*.tif")
    return sorted(glob.glob(pattern))


def compute_zonal_mean(tiff_path, geometry):
    with rasterio.open(tiff_path) as ds:
        try:
            out_image, _ = rasterio_mask(
                ds, [mapping(geometry)], crop=True, nodata=-3000
            )
        except (ValueError, Exception):
            return np.nan

    valid = out_image[0]
    valid = valid[(valid > -2000) & (valid < 10000)]
    if len(valid) == 0:
        return np.nan
    return float(np.mean(valid))


def main():
    print("Loading county boundaries...", flush=True)
    counties_gdf = gpd.read_file(str(COUNTY_GEOJSON))
    if counties_gdf.crs is None or str(counties_gdf.crs) != DST_CRS:
        counties_gdf = counties_gdf.to_crs(DST_CRS)
    print(f"  Loaded {len(counties_gdf)} counties", flush=True)

    counties_proj = counties_gdf.to_crs("EPSG:3826")
    area_ha = {row["COUNTYCODE"]: round(row.geometry.area / 10000, 1)
               for _, row in counties_proj.iterrows()}

    results = {}
    for _, row in counties_gdf.iterrows():
        code = row["COUNTYCODE"]
        results[code] = {
            "code": code,
            "name": row["COUNTYNAME"],
            "name_en": row["COUNTYENG"],
            "area_ha": area_ha.get(code, 0),
            "ndvi_dates": {},
        }

    if OUTPUT_JSON.exists():
        with open(OUTPUT_JSON, encoding="utf-8") as f:
            prev = json.load(f)
        for county in prev.get("counties", []):
            code = county.get("code")
            if code in results:
                results[code]["ndvi_dates"] = county.get("ndvi_dates", {})
        print(f"  Loaded previous results", flush=True)

    tiff_files = find_tiff_files()
    if not tiff_files:
        print("No GeoTIFF files found in data/tiff/. "
              "Run extract_ndvi_tiff.py first.", flush=True)
        return

    existing_dates = set()
    for code in results:
        existing_dates.update(results[code]["ndvi_dates"].keys())

    new_tiffs = []
    for tiff_path in tiff_files:
        date_str = Path(tiff_path).stem
        if date_str not in existing_dates:
            new_tiffs.append(tiff_path)

    if not new_tiffs:
        print(f"\nAll {len(tiff_files)} composites already processed.", flush=True)
    else:
        print(f"\n{len(new_tiffs)} new composites to process "
              f"({len(tiff_files) - len(new_tiffs)} already done)", flush=True)

        for i, tiff_path in enumerate(new_tiffs, 1):
            date_str = Path(tiff_path).stem
            print(f"  [{i}/{len(new_tiffs)}] {date_str}...", flush=True)

            for _, row in counties_gdf.iterrows():
                code = row["COUNTYCODE"]
                mean_val = compute_zonal_mean(tiff_path, row.geometry)
                if not np.isnan(mean_val):
                    ndvi = mean_val * NDVI_SCALE
                    if 0 < ndvi < 1:
                        results[code]["ndvi_dates"][date_str] = round(ndvi, 4)

    from datetime import datetime as _dt
    years = range(2016, _dt.now().year + 1)

    counties_out = []
    for code, county in results.items():
        dates = county["ndvi_dates"]
        ndvi_yearly = {}
        green_ha_yearly = {}
        for year in years:
            ys = str(year)
            year_vals = [v for d, v in dates.items() if d.startswith(ys)]
            if year_vals:
                mean_ndvi = round(float(np.mean(year_vals)), 4)
                ndvi_yearly[ys] = mean_ndvi
                green_ha_yearly[ys] = round(mean_ndvi * county["area_ha"], 1)

        counties_out.append({
            "code": county["code"],
            "name": county["name"],
            "name_en": county["name_en"],
            "area_ha": county["area_ha"],
            "ndvi_dates": dict(sorted(dates.items())),
            "ndvi_yearly": ndvi_yearly,
            "green_ha_yearly": green_ha_yearly,
        })

    output = {
        "metadata": {
            "product": "MOD13Q1 v061",
            "resolution": "250m",
            "temporal_composite": "16-day",
            "aggregation": "per composite + yearly mean",
            "date_range": f"{years.start}-{years.stop - 1}",
            "generated": str(np.datetime64("today")),
        },
        "counties": counties_out,
    }

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\nOutput written to {OUTPUT_JSON}", flush=True)

    print("\nSummary:", flush=True)
    for county in counties_out:
        n_dates = len(county["ndvi_dates"])
        n_years = len(county["ndvi_yearly"])
        if n_dates > 0:
            vals = list(county["ndvi_yearly"].values())
            print(
                f"  {county['name']} ({county['name_en']}): "
                f"{n_dates} composites, {n_years} years, "
                f"NDVI {min(vals):.4f}-{max(vals):.4f}",
                flush=True,
            )
        else:
            print(f"  {county['name']} ({county['name_en']}): no data",
                  flush=True)


if __name__ == "__main__":
    main()
