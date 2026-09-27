#!/usr/bin/env python3
"""
Extract NDVI and pixel reliability bands from MODIS HDF4 files,
reproject to EPSG:4326, mosaic h28v06+h29v06 tiles, apply QA filter,
and save as a single GeoTIFF per composite date.

Output: data/tiff/{YYYY-MM-DD}.tif (one band, int16, QA-filtered NDVI)
After successful extraction, the source HDF files are deleted.
"""

import os
import glob
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timedelta

import numpy as np
from osgeo import gdal, osr
import rasterio
from rasterio.merge import merge
from rasterio.io import MemoryFile
from rasterio.transform import Affine

gdal.UseExceptions()

PROJECT_DIR = Path(__file__).parent.parent
RAW_DIR = PROJECT_DIR / "data" / "raw"
TIFF_DIR = PROJECT_DIR / "data" / "tiff"

NDVI_SUBDATASET = "250m 16 days NDVI"
QA_SUBDATASET = "250m 16 days pixel reliability"
DST_CRS = "EPSG:4326"

MODIS_SINU_WKT = osr.SpatialReference()
MODIS_SINU_WKT.ImportFromProj4(
    "+proj=sinu +lon_0=0 +x_0=0 +y_0=0 +R=6371007.181 +units=m +no_defs"
)
MODIS_SINU_STR = MODIS_SINU_WKT.ExportToWkt()


def julian_to_date(date_key):
    year = int(date_key[1:5])
    jday = int(date_key[5:8])
    d = datetime(year, 1, 1) + timedelta(days=jday - 1)
    return d.strftime("%Y-%m-%d")


def get_subdataset_path(hdf_path, subdataset_name):
    ds = gdal.Open(hdf_path)
    if ds is None:
        return None
    subs = ds.GetSubDatasets()
    ds = None
    for path, desc in subs:
        if subdataset_name in desc:
            return path
    return None


def read_and_reproject(subdataset_path, nodata=-3000):
    src_ds = gdal.Open(subdataset_path)
    if src_ds is None:
        return None, None

    src_proj = src_ds.GetProjection()
    if not src_proj:
        src_proj = MODIS_SINU_STR

    dst_srs = osr.SpatialReference()
    dst_srs.SetFromUserInput(DST_CRS)

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
        "dtype": "int16",
        "width": w,
        "height": h,
        "count": 1,
        "crs": DST_CRS,
        "transform": transform,
        "nodata": -3000,
        "compress": "deflate",
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
            ds.write(data.astype(np.int16))
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


def process_composite(hdf_files, output_path):
    """Extract, reproject, mosaic, QA-filter, and save one composite."""
    tile_data = []
    for hdf_path in hdf_files:
        ndvi_sub = get_subdataset_path(hdf_path, NDVI_SUBDATASET)
        qa_sub = get_subdataset_path(hdf_path, QA_SUBDATASET)
        if ndvi_sub is None:
            print(f"    Warning: No NDVI subdataset in {hdf_path}", flush=True)
            continue

        ndvi_data, ndvi_profile = read_and_reproject(ndvi_sub, nodata=-3000)
        if ndvi_data is None:
            continue

        if qa_sub:
            qa_data, _ = read_and_reproject(qa_sub, nodata=255)
            if qa_data is not None:
                bad_mask = (qa_data[0] > 1) | (qa_data[0] < 0)
                ndvi_data[0][bad_mask] = -3000

        tile_data.append((ndvi_data, ndvi_profile))

    if not tile_data:
        return False

    mosaic_data, mosaic_profile = mosaic_tiles(tile_data)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(output_path, "w", **mosaic_profile) as dst:
        dst.write(mosaic_data.astype(np.int16))

    return True


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
    TIFF_DIR.mkdir(parents=True, exist_ok=True)

    year_dirs = sorted(
        d for d in RAW_DIR.iterdir()
        if d.is_dir() and d.name.isdigit()
    )

    total_extracted = 0
    total_skipped = 0
    total_deleted = 0

    for year_dir in year_dirs:
        year = year_dir.name
        hdf_files = sorted(glob.glob(str(year_dir / "*.hdf")))
        if not hdf_files:
            continue

        date_groups = group_hdf_by_date(hdf_files)
        print(f"\n{year}: {len(hdf_files)} HDF files, "
              f"{len(date_groups)} composites", flush=True)

        for date_key in sorted(date_groups.keys()):
            files = date_groups[date_key]
            iso_date = julian_to_date(date_key)
            tiff_path = TIFF_DIR / f"{iso_date}.tif"

            if tiff_path.exists() and tiff_path.stat().st_size > 1000:
                total_skipped += 1
                for f in files:
                    os.remove(f)
                    total_deleted += 1
                continue

            print(f"  {date_key} ({iso_date}): extracting...", flush=True)
            if process_composite(files, tiff_path):
                total_extracted += 1
                for f in files:
                    os.remove(f)
                    total_deleted += 1
                size_mb = tiff_path.stat().st_size / (1024 * 1024)
                print(f"    -> {tiff_path.name} ({size_mb:.1f} MB)", flush=True)
            else:
                print(f"    Warning: failed to extract {date_key}", flush=True)

    # Clean up empty year directories
    for year_dir in year_dirs:
        remaining = list(year_dir.glob("*.hdf"))
        if not remaining and year_dir.exists():
            year_dir.rmdir()

    print(f"\nDone: {total_extracted} extracted, {total_skipped} skipped, "
          f"{total_deleted} HDF files deleted", flush=True)


if __name__ == "__main__":
    main()
