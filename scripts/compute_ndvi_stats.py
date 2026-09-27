#!/usr/bin/env python3
"""
Compute yearly mean NDVI per Taiwan county from MODIS MOD13Q1 HDF4 tiles.

Reads downloaded HDF4 files from data/raw/, computes zonal statistics
against county boundaries, and outputs data/county_ndvi.json.
"""

import os
import json
import glob
import warnings
from pathlib import Path
from collections import defaultdict

import numpy as np
import geopandas as gpd
import rasterio
from rasterio.warp import calculate_default_transform, reproject, Resampling
from rasterio.mask import mask as rasterio_mask
from rasterio.merge import merge
from rasterio.io import MemoryFile
from shapely.geometry import mapping

warnings.filterwarnings("ignore", category=rasterio.errors.NotGeoreferencedWarning)

PROJECT_DIR = Path(__file__).parent.parent
DATA_DIR = PROJECT_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
COUNTY_GEOJSON = Path("/home/kiang/public_html/taiwan_basecode/county/geo/20200820.json")
OUTPUT_JSON = DATA_DIR / "county_ndvi.json"

NDVI_SUBDATASET = "250m 16 days NDVI"
QA_SUBDATASET = "250m 16 days pixel reliability"
NDVI_SCALE = 0.0001
DST_CRS = "EPSG:4326"

YEARS = range(2016, 2026)


def find_hdf_files(year):
    """Find all HDF4 files for a given year."""
    pattern = str(RAW_DIR / str(year) / "*.hdf")
    return sorted(glob.glob(pattern))


def get_subdataset(hdf_path, subdataset_name):
    """Get the path to a specific subdataset within an HDF4 file."""
    with rasterio.open(hdf_path) as ds:
        for name, desc in ds.subdatasets:
            if subdataset_name in desc or subdataset_name in name:
                return name
    return None


def read_and_reproject(subdataset_path):
    """Read a MODIS subdataset and reproject to EPSG:4326."""
    with rasterio.open(subdataset_path) as src:
        src_crs = src.crs
        if src_crs is None:
            from rasterio.crs import CRS
            src_crs = CRS.from_proj4(
                "+proj=sinu +lon_0=0 +x_0=0 +y_0=0 "
                "+R=6371007.181 +units=m +no_defs"
            )

        transform, width, height = calculate_default_transform(
            src_crs, DST_CRS, src.width, src.height, *src.bounds
        )
        dst_data = np.empty((1, height, width), dtype=np.float32)
        reproject(
            source=rasterio.band(src, 1),
            destination=dst_data[0],
            src_transform=src.transform,
            src_crs=src_crs,
            dst_transform=transform,
            dst_crs=DST_CRS,
            resampling=Resampling.nearest,
        )

    profile = {
        "driver": "GTiff",
        "dtype": "float32",
        "width": width,
        "height": height,
        "count": 1,
        "crs": DST_CRS,
        "transform": transform,
        "nodata": -3000,
    }
    return dst_data, profile


def mosaic_tiles(tile_data_list):
    """Mosaic multiple reprojected tiles into one raster."""
    if len(tile_data_list) == 1:
        return tile_data_list[0]

    memfiles = []
    datasets = []
    for data, profile in tile_data_list:
        memfile = MemoryFile()
        with memfile.open(**profile) as ds:
            ds.write(data)
        datasets.append(memfile.open())
        memfiles.append(memfile)

    mosaic_data, mosaic_transform = merge(datasets)
    profile = datasets[0].profile.copy()
    profile.update({
        "width": mosaic_data.shape[2],
        "height": mosaic_data.shape[1],
        "transform": mosaic_transform,
    })

    for ds in datasets:
        ds.close()
    for mf in memfiles:
        mf.close()

    return mosaic_data, profile


def compute_zonal_mean(data, profile, geometry):
    """Compute mean value within a polygon geometry."""
    with MemoryFile() as memfile:
        with memfile.open(**profile) as ds:
            ds.write(data)
        with memfile.open() as ds:
            try:
                out_image, _ = rasterio_mask(
                    ds, [mapping(geometry)], crop=True, nodata=-3000
                )
            except ValueError:
                return np.nan

    valid = out_image[0]
    valid = valid[(valid > -2000) & (valid < 10000)]
    if len(valid) == 0:
        return np.nan
    return float(np.mean(valid))


def process_date(hdf_files, counties_gdf):
    """Process a set of tile HDF files for one date, return NDVI per county."""
    tile_data = []
    for hdf_path in hdf_files:
        ndvi_sub = get_subdataset(hdf_path, NDVI_SUBDATASET)
        qa_sub = get_subdataset(hdf_path, QA_SUBDATASET)
        if ndvi_sub is None:
            print(f"    Warning: No NDVI subdataset in {hdf_path}")
            continue

        ndvi_data, ndvi_profile = read_and_reproject(ndvi_sub)

        if qa_sub:
            qa_data, _ = read_and_reproject(qa_sub)
            bad_mask = (qa_data[0] > 1) | (qa_data[0] < 0)
            ndvi_data[0][bad_mask] = -3000

        tile_data.append((ndvi_data, ndvi_profile))

    if not tile_data:
        return {}

    mosaic_data, mosaic_profile = mosaic_tiles(tile_data)

    county_ndvi = {}
    for _, row in counties_gdf.iterrows():
        code = row["COUNTYCODE"]
        mean_val = compute_zonal_mean(mosaic_data, mosaic_profile, row.geometry)
        if not np.isnan(mean_val):
            county_ndvi[code] = mean_val * NDVI_SCALE
    return county_ndvi


def group_hdf_by_date(hdf_files):
    """Group HDF files by their acquisition date (Julian day in filename)."""
    date_groups = defaultdict(list)
    for f in hdf_files:
        basename = os.path.basename(f)
        parts = basename.split(".")
        if len(parts) >= 2:
            date_key = parts[1]  # e.g., A2016001
            date_groups[date_key].append(f)
    return dict(date_groups)


def main():
    print("Loading county boundaries...")
    counties_gdf = gpd.read_file(str(COUNTY_GEOJSON))
    if counties_gdf.crs is None or str(counties_gdf.crs) != DST_CRS:
        counties_gdf = counties_gdf.to_crs(DST_CRS)
    print(f"  Loaded {len(counties_gdf)} counties")

    results = {}
    for _, row in counties_gdf.iterrows():
        results[row["COUNTYCODE"]] = {
            "code": row["COUNTYCODE"],
            "name": row["COUNTYNAME"],
            "name_en": row["COUNTYENG"],
            "ndvi_yearly": {},
        }

    for year in YEARS:
        print(f"\nProcessing {year}...")
        hdf_files = find_hdf_files(year)
        if not hdf_files:
            print(f"  No HDF files found for {year}, skipping")
            continue

        date_groups = group_hdf_by_date(hdf_files)
        print(f"  Found {len(hdf_files)} files in {len(date_groups)} composites")

        yearly_values = defaultdict(list)
        for i, (date_key, files) in enumerate(sorted(date_groups.items()), 1):
            print(f"  [{i}/{len(date_groups)}] Processing {date_key}...")
            county_ndvi = process_date(files, counties_gdf)
            for code, val in county_ndvi.items():
                if 0 < val < 1:
                    yearly_values[code].append(val)

        for code, values in yearly_values.items():
            if values:
                results[code]["ndvi_yearly"][str(year)] = round(
                    float(np.mean(values)), 4
                )

    output = {
        "metadata": {
            "product": "MOD13Q1 v061",
            "resolution": "250m",
            "temporal_composite": "16-day",
            "aggregation": "yearly mean",
            "date_range": f"{YEARS.start}-{YEARS.stop - 1}",
            "generated": str(np.datetime64("today")),
        },
        "counties": list(results.values()),
    }

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\nOutput written to {OUTPUT_JSON}")

    print("\nSummary:")
    for county in output["counties"]:
        years_with_data = len(county["ndvi_yearly"])
        if years_with_data > 0:
            vals = list(county["ndvi_yearly"].values())
            print(
                f"  {county['name']} ({county['name_en']}): "
                f"{years_with_data} years, "
                f"range {min(vals):.4f}-{max(vals):.4f}"
            )
        else:
            print(f"  {county['name']} ({county['name_en']}): no data")


if __name__ == "__main__":
    main()
