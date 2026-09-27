#!/usr/bin/env python3
"""
Generate static HTML site from computed NDVI data and county GeoJSON.

Reads data/county_ndvi.json and the county GeoJSON, then produces
docs/index.html with embedded data for the Leaflet map and Chart.js charts.
"""

import json
import os
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
DATA_DIR = PROJECT_DIR / "data"
DOCS_DIR = PROJECT_DIR / "docs"
COUNTY_GEOJSON = Path("/home/kiang/public_html/taiwan_basecode/county/geo/20200820.json")
NDVI_JSON = DATA_DIR / "county_ndvi.json"


def simplify_geojson(geojson_data):
    """Keep only essential properties for the web map."""
    simplified = {
        "type": "FeatureCollection",
        "features": []
    }
    for feature in geojson_data["features"]:
        simplified["features"].append({
            "type": "Feature",
            "properties": {
                "COUNTYCODE": feature["properties"]["COUNTYCODE"],
                "COUNTYNAME": feature["properties"]["COUNTYNAME"],
                "COUNTYENG": feature["properties"]["COUNTYENG"],
            },
            "geometry": feature["geometry"],
        })
    return simplified


def main():
    print("Loading data...")
    with open(COUNTY_GEOJSON, encoding="utf-8") as f:
        geojson_data = json.load(f)

    if NDVI_JSON.exists():
        with open(NDVI_JSON, encoding="utf-8") as f:
            ndvi_data = json.load(f)
    else:
        print("Warning: county_ndvi.json not found, using sample data")
        ndvi_data = generate_sample_data(geojson_data)

    simplified_geo = simplify_geojson(geojson_data)

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    (DOCS_DIR / "data").mkdir(exist_ok=True)

    with open(DOCS_DIR / "data" / "county_ndvi.json", "w", encoding="utf-8") as f:
        json.dump(ndvi_data, f, ensure_ascii=False)

    with open(DOCS_DIR / "data" / "counties.geojson", "w", encoding="utf-8") as f:
        json.dump(simplified_geo, f, ensure_ascii=False)

    print(f"Site generated in {DOCS_DIR}")
    print(f"  data/county_ndvi.json")
    print(f"  data/counties.geojson")


def generate_sample_data(geojson_data):
    """Generate plausible sample NDVI data for development/preview."""
    import random
    random.seed(42)
    counties = []
    for feature in geojson_data["features"]:
        props = feature["properties"]
        base = random.uniform(0.35, 0.65)
        trend = random.uniform(-0.008, 0.005)
        ndvi_yearly = {}
        for year in range(2016, 2026):
            noise = random.uniform(-0.02, 0.02)
            val = base + trend * (year - 2016) + noise
            ndvi_yearly[str(year)] = round(max(0.1, min(0.9, val)), 4)
        counties.append({
            "code": props["COUNTYCODE"],
            "name": props["COUNTYNAME"],
            "name_en": props["COUNTYENG"],
            "ndvi_yearly": ndvi_yearly,
        })
    return {
        "metadata": {
            "product": "MOD13Q1 v061 (SAMPLE DATA)",
            "resolution": "250m",
            "temporal_composite": "16-day",
            "aggregation": "yearly mean",
            "date_range": "2016-2025",
            "generated": "sample",
        },
        "counties": counties,
    }


if __name__ == "__main__":
    main()
