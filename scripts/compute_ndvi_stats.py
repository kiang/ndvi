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
from osgeo import gdal, osr
import rasterio
from rasterio.mask import mask as rasterio_mask
from rasterio.merge import merge
from rasterio.io import MemoryFile
from rasterio.transform import Affine
from shapely.geometry import mapping

gdal.UseExceptions()
warnings.filterwarnings("ignore")

PROJECT_DIR = Path(__file__).parent.parent
DATA_DIR = PROJECT_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
COUNTY_GEOJSON = Path("/home/kiang/public_html/taiwan_basecode/county/geo/20200820.json")
OUTPUT_JSON = DATA_DIR / "county_ndvi.json"

NDVI_SUBDATASET = "250m 16 days NDVI"
QA_SUBDATASET = "250m 16 days pixel reliability"
NDVI_SCALE = 0.0001
DST_CRS = "EPSG:4326"

YEARS = range(2016, 2026)

MODIS_SINU_WKT = osr.SpatialReference()
MODIS_SINU_WKT.ImportFromProj4(
    "+proj=sinu +lon_0=0 +x_0=0 +y_0=0 +R=6371007.181 +units=m +no_defs"
)
MODIS_SINU_STR = MODIS_SINU_WKT.ExportToWkt()


def find_hdf_files(year):
    pattern = str(RAW_DIR / str(year) / "*.hdf")
    return sorted(glob.glob(pattern))


def get_subdataset_path(hdf_path, subdataset_name):
    """Get the GDAL subdataset path from an HDF4 file."""
    ds = gdal.Open(hdf_path)
    if ds is None:
        return None
    subs = ds.GetSubDatasets()
    ds = None
    for path, desc in subs:
        if subdataset_name in desc:
            return path
    return None


def read_and_reproject_gdal(subdataset_path):
    """Read a MODIS subdataset via GDAL and reproject to EPSG:4326.
    Returns (data_array[1,H,W], rasterio_profile)."""
    src_ds = gdal.Open(subdataset_path)
    if src_ds is None:
        return None, None

    src_proj = src_ds.GetProjection()
    if not src_proj:
        src_proj = MODIS_SINU_STR

    dst_srs = osr.SpatialReference()
    dst_srs.SetFromUserInput(DST_CRS)

    src_dt = src_ds.GetRasterBand(1).DataType
    nodata = -3000 if src_dt in (gdal.GDT_Int16, gdal.GDT_Int32, gdal.GDT_Float32, gdal.GDT_Float64) else 255
    warp_opts = gdal.WarpOptions(
        format="MEM",
        srcSRS=src_proj,
        dstSRS=dst_srs.ExportToWkt(),
        resampleAlg=gdal.GRA_NearestNeighbour,
        dstNodata=nodata,
    )
    dst_ds = gdal.Warp("", src_ds, options=warp_opts)
    src_ds = None

    if dst_ds is None:
        return None, None

    data = dst_ds.ReadAsArray().astype(np.float32)
    gt = dst_ds.GetGeoTransform()
    w, h = dst_ds.RasterXSize, dst_ds.RasterYSize
    dst_ds = None

    transform = Affine.from_gdal(*gt)
    profile = {
        "driver": "GTiff",
        "dtype": "float32",
        "width": w,
        "height": h,
        "count": 1,
        "crs": DST_CRS,
        "transform": transform,
        "nodata": float(nodata),
    }
    return data.reshape(1, h, w), profile


def mosaic_tiles(tile_data_list):
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
    with MemoryFile() as memfile:
        with memfile.open(**profile) as ds:
            ds.write(data)
        with memfile.open() as ds:
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


def process_date(hdf_files, counties_gdf):
    tile_data = []
    for hdf_path in hdf_files:
        ndvi_sub = get_subdataset_path(hdf_path, NDVI_SUBDATASET)
        qa_sub = get_subdataset_path(hdf_path, QA_SUBDATASET)
        if ndvi_sub is None:
            print(f"    Warning: No NDVI subdataset in {hdf_path}")
            continue

        ndvi_data, ndvi_profile = read_and_reproject_gdal(ndvi_sub)
        if ndvi_data is None:
            print(f"    Warning: Failed to reproject {hdf_path}")
            continue

        if qa_sub:
            qa_data, _ = read_and_reproject_gdal(qa_sub)
            if qa_data is not None:
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
    date_groups = defaultdict(list)
    for f in hdf_files:
        basename = os.path.basename(f)
        parts = basename.split(".")
        if len(parts) >= 2:
            date_key = parts[1]
            date_groups[date_key].append(f)
    return dict(date_groups)


def main():
    print("Loading county boundaries...")
    counties_gdf = gpd.read_file(str(COUNTY_GEOJSON))
    if counties_gdf.crs is None or str(counties_gdf.crs) != DST_CRS:
        counties_gdf = counties_gdf.to_crs(DST_CRS)
    print(f"  Loaded {len(counties_gdf)} counties")

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
            "ndvi_yearly": {},
            "green_ha_yearly": {},
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
                mean_ndvi = round(float(np.mean(values)), 4)
                results[code]["ndvi_yearly"][str(year)] = mean_ndvi
                county_area = results[code]["area_ha"]
                results[code]["green_ha_yearly"][str(year)] = round(
                    mean_ndvi * county_area, 1
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
            green_vals = list(county["green_ha_yearly"].values())
            print(
                f"  {county['name']} ({county['name_en']}): "
                f"{years_with_data} years, NDVI {min(vals):.4f}-{max(vals):.4f}, "
                f"green {min(green_vals):,.0f}-{max(green_vals):,.0f} ha"
            )
        else:
            print(f"  {county['name']} ({county['name_en']}): no data")


if __name__ == "__main__":
    main()
