#!/usr/bin/env python3
"""
Download MODIS MOD13Q1 (250m NDVI) tiles for Taiwan from NASA LP DAAC.

Tiles needed: h28v06 and h29v06
Date range: 2016-01-01 to 2025-12-31

Prerequisites:
  - NASA Earthdata account: https://urs.earthdata.nasa.gov/
  - Set environment variables EARTHDATA_USER and EARTHDATA_PASS,
    or configure ~/.netrc with:
      machine urs.earthdata.nasa.gov login <user> password <pass>
"""

import os
import sys
import json
import time
import requests
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse

DATA_DIR = Path(__file__).parent.parent / "data" / "raw"
TILES = ["h28v06", "h29v06"]
START_DATE = "2016-01-01"
END_DATE = "2025-12-31"
PRODUCT = "MOD13Q1"
VERSION = "061"

CMR_URL = "https://cmr.earthdata.nasa.gov/search/granules.json"
CMR_PAGE_SIZE = 200

def get_earthdata_session():
    session = requests.Session()
    token = os.environ.get("EARTHDATA_TOKEN")
    if token:
        session.headers["Authorization"] = f"Bearer {token}"
        return session
    user = os.environ.get("EARTHDATA_USER")
    passwd = os.environ.get("EARTHDATA_PASS")
    if user and passwd:
        session.auth = (user, passwd)
    return session

TAIWAN_BBOX = "119,21,123,26"

def search_granules():
    """Search CMR for MOD13Q1 granules covering Taiwan."""
    all_links = []
    page = 1
    while True:
        params = {
            "short_name": PRODUCT,
            "version": VERSION,
            "temporal": f"{START_DATE}T00:00:00Z,{END_DATE}T23:59:59Z",
            "bounding_box": TAIWAN_BBOX,
            "page_size": CMR_PAGE_SIZE,
            "page_num": page,
            "sort_key": "start_date",
        }
        resp = requests.get(CMR_URL, params=params, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        entries = data.get("feed", {}).get("entry", [])
        if not entries:
            break
        for entry in entries:
            for link in entry.get("links", []):
                href = link.get("href", "")
                if href.endswith(".hdf"):
                    filename = os.path.basename(urlparse(href).path)
                    if any(t in filename for t in TILES):
                        all_links.append({
                            "url": href,
                            "filename": filename,
                            "date": entry.get("time_start", "")[:10],
                        })
                    break
        if len(entries) < CMR_PAGE_SIZE:
            break
        page += 1
        time.sleep(0.5)
    return all_links

def download_file(session, url, dest_path):
    """Download a file with Earthdata authentication (handles redirects)."""
    if dest_path.exists() and dest_path.stat().st_size > 1000:
        return True
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest_path.with_suffix(".tmp")
    try:
        resp = session.get(url, stream=True, timeout=300, allow_redirects=True)
        if resp.status_code == 401:
            resp = session.get(url, allow_redirects=False, timeout=60)
            if resp.status_code in (301, 302, 303, 307):
                redirect_url = resp.headers.get("Location")
                resp = session.get(redirect_url, stream=True, timeout=300)
        resp.raise_for_status()
        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        with open(tmp_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
                downloaded += len(chunk)
        if total > 0 and downloaded < total * 0.9:
            raise Exception(f"Incomplete download: {downloaded}/{total}")
        tmp_path.rename(dest_path)
        return True
    except Exception as e:
        print(f"  Error downloading {url}: {e}")
        if tmp_path.exists():
            tmp_path.unlink()
        return False

def main():
    session = get_earthdata_session()
    has_auth = "Authorization" in session.headers or session.auth
    if not has_auth:
        import netrc
        try:
            n = netrc.netrc()
            auth = n.authenticators("urs.earthdata.nasa.gov")
            if auth:
                session.auth = (auth[0], auth[2])
                has_auth = True
        except (FileNotFoundError, netrc.NetrcParseError):
            pass

    if not has_auth:
        print("Error: No Earthdata credentials found.")
        print("Set EARTHDATA_TOKEN (Bearer JWT token),")
        print("or EARTHDATA_USER and EARTHDATA_PASS,")
        print("or configure ~/.netrc with machine urs.earthdata.nasa.gov")
        sys.exit(1)

    print(f"Searching for {PRODUCT} v{VERSION} granules covering Taiwan...")
    all_granules = search_granules()
    print(f"  Found {len(all_granules)} granules")

    print(f"\nTotal granules to download: {len(all_granules)}")

    downloaded = 0
    skipped = 0
    failed = 0
    for i, granule in enumerate(all_granules, 1):
        year = granule["date"][:4]
        dest = DATA_DIR / year / granule["filename"]
        if dest.exists():
            skipped += 1
            continue
        print(f"  [{i}/{len(all_granules)}] Downloading {granule['filename']}...")
        if download_file(session, granule["url"], dest):
            downloaded += 1
        else:
            failed += 1
        if i % 10 == 0:
            time.sleep(1)

    print(f"\nDone: {downloaded} downloaded, {skipped} skipped (existing), {failed} failed")

if __name__ == "__main__":
    main()
