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

    if not NDVI_JSON.exists():
        print("Error: county_ndvi.json not found. Run compute_ndvi_stats.py first.")
        return
    with open(NDVI_JSON, encoding="utf-8") as f:
        ndvi_data = json.load(f)

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


if __name__ == "__main__":
    main()
