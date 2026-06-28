#!/usr/bin/env python3
"""
Fetch HRRR wind UGRD/VGRD at 925 mb for Gore Mountain and write JSON.

Output file: region northeast/gore_hrrr_wind_925mb_YYYYMMDDtHHz.json

Requirements: xarray, cfgrib, eccodes, requests, numpy
"""

import os
import sys
import json
import tempfile
import shutil
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

try:
    import xarray as xr
except Exception:
    xr = None

import numpy as np

LOG = logging.getLogger('hrrr_wind_925mb')
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

# Gore Mountain coords (lon, lat)
GORE_COORDS = (-74.0069444, 43.6722222)

NE_BBOX = dict(leftlon=-82, rightlon=-66, toplat=47, bottomlat=38)

BASE_URL = 'https://nomads.ncep.noaa.gov/cgi-bin/filter_hrrr_2d.pl'
HEADERS = {'User-Agent': 'python-requests/1.0'}

SPECIAL_RUNS = {'00', '06', '12', '18'}


def build_url(date_ymd, hour_str, fhr, bbox=NE_BBOX):
    ffs = f"{fhr:02d}"
    dir_path = f"/hrrr.{date_ymd}/conus"
    filename = f"hrrr.t{hour_str}z.wrfsfcf{ffs}.grib2"
    params = {
        'dir': dir_path,
        'file': filename,
        'var_UGRD': 'on',
        'var_VGRD': 'on',
        'lev_925_mb': 'on',
        'leftlon': str(bbox['leftlon']),
        'rightlon': str(bbox['rightlon']),
        'toplat': str(bbox['toplat']),
        'bottomlat': str(bbox['bottomlat']),
    }
    return BASE_URL, params


def url_exists(url, params, timeout=10):
    try:
        r = requests.get(url, params=params, stream=True, timeout=timeout, headers=HEADERS)
        if r.status_code != 200:
            r.close()
            return False
        chunk = r.raw.read(64)
        r.close()
        return bool(chunk)
    except Exception:
        return False


def download_file(url, params, target_dir, timeout=120):
    fn = params.get('file')
    if not fn:
        return None
    target = os.path.join(target_dir, fn)
    try:
        r = requests.get(url, params=params, stream=True, timeout=timeout, headers=HEADERS)
        if r.status_code != 200:
            return None
        with open(target, 'wb') as fh:
            for chunk in r.iter_content(8192):
                if chunk:
                    fh.write(chunk)
        if os.path.getsize(target) < 10240:
            os.remove(target)
            return None
        return target
    except Exception:
        return None


def find_var(ds, candidates):
    for name in ds.data_vars:
        lname = name.lower()
        for c in candidates:
            if c in lname:
                return name
    return None


def wind_dir_from_uv(u, v):
    try:
        deg = (np.degrees(np.arctan2(-u, -v)) + 360.0) % 360.0
        return float(deg)
    except Exception:
        return None


def deg_to_cardinal(deg):
    if deg is None:
        return ''
    dirs = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
    ix = int((deg + 22.5) // 45) % 8
    return dirs[ix]


def get_wind_at_point(ds, u_name, v_name, lat, lon):
    try:
        u_arr = ds[u_name]
        v_arr = ds[v_name]

        lat_names = [n for n in ds.coords.keys() if 'lat' in n.lower()]
        lon_names = [n for n in ds.coords.keys() if 'lon' in n.lower()]
        if lat_names and lon_names:
            latn = lat_names[0]
            lonn = lon_names[0]
            lats = ds[latn].values
            lons = ds[lonn].values
            if np.any(lons > 180):
                lons_norm = np.where(lons > 180, lons - 360, lons)
            else:
                lons_norm = lons

            if getattr(lats, 'ndim', 1) == 2 and getattr(lons_norm, 'ndim', 1) == 2:
                dist = (lats - lat) ** 2 + (lons_norm - lon) ** 2
                iy, ix = np.unravel_index(np.argmin(dist), dist.shape)
                arr_u = np.squeeze(u_arr.values)
                arr_v = np.squeeze(v_arr.values)
                uval = float(arr_u[iy, ix])
                vval = float(arr_v[iy, ix])
            else:
                iy = int(np.abs(lats - lat).argmin())
                ix = int(np.abs(lons_norm - lon).argmin())
                arr_u = np.squeeze(u_arr.values)
                arr_v = np.squeeze(v_arr.values)
                if arr_u.ndim == 2:
                    uval = float(arr_u[iy, ix])
                    vval = float(arr_v[iy, ix])
                elif arr_u.ndim == 1:
                    uval = float(arr_u[iy])
                    vval = float(arr_v[iy])
                else:
                    uval = float(np.ravel(arr_u)[0])
                    vval = float(np.ravel(arr_v)[0])
        else:
            arr_u = np.squeeze(u_arr.values)
            arr_v = np.squeeze(v_arr.values)
            uval = float(np.ravel(arr_u)[0])
            vval = float(np.ravel(arr_v)[0])
    except Exception:
        return None, None, None, ''

    speed_mps = float(np.sqrt(uval * uval + vval * vval))
    speed_mph = speed_mps * 2.2369362920544
    dir_deg = wind_dir_from_uv(uval, vval)
    dir_card = deg_to_cardinal(dir_deg)
    return round(speed_mps, 3), round(speed_mph, 2), round(dir_deg, 1), dir_card


def main():
    now = datetime.utcnow()
    date_ymd = None
    hour = None
    for dh in range(0, 72 + 1):
        t = now - timedelta(hours=dh)
        d = t.strftime('%Y%m%d')
        h = t.strftime('%H')
        url, params = build_url(d, h, 0)
        if url_exists(url, params):
            date_ymd, hour = d, h
            LOG.info('Found available run (probe): %sT%sZ', d, h)
            break
    if not date_ymd:
        LOG.error('No run found')
        sys.exit(1)

    max_fallbacks = 6
    attempt = 0
    chosen_date = date_ymd
    chosen_hour = hour

    while attempt < max_fallbacks:
        run_is_special = chosen_hour in SPECIAL_RUNS
        max_f = 48 if run_is_special else 18
        missing = False
        for fhr in range(0, max_f + 1):
            url, params = build_url(chosen_date, chosen_hour, fhr)
            if not url_exists(url, params):
                LOG.warning('Probe missing for run %sT%sZ file %s', chosen_date, chosen_hour, params.get('file'))
                missing = True
                break
        if not missing:
            date_ymd, hour = chosen_date, chosen_hour
            run_is_special = chosen_hour in SPECIAL_RUNS
            max_f = 48 if run_is_special else 18
            break
        LOG.info('Falling back one run from %sT%sZ', chosen_date, chosen_hour)
        dt = datetime.strptime(f"%s%s" % (chosen_date, chosen_hour), '%Y%m%d%H') - timedelta(hours=1)
        chosen_date = dt.strftime('%Y%m%d')
        chosen_hour = dt.strftime('%H')
        attempt += 1

    if attempt >= max_fallbacks and missing:
        LOG.error('No complete run found after %d fallbacks', max_fallbacks)
        sys.exit(1)

    out_dir = os.path.join(os.getcwd(), 'region northeast')
    os.makedirs(out_dir, exist_ok=True)
    out_file = os.path.join(out_dir, f'gore_hrrr_wind_925mb_{date_ymd}t{hour}z.json')

    records = []
    tmpdir = tempfile.mkdtemp(prefix='hrrrwind925_')
    try:
        run_is_special = hour in SPECIAL_RUNS
        max_f = 48 if run_is_special else 18
        for fhr in range(0, max_f + 1):
            url, params = build_url(date_ymd, hour, fhr)
            LOG.info('Processing %s', params.get('file'))
            if not url_exists(url, params):
                LOG.warning('Missing %s', params.get('file'))
                continue
            tgt = download_file(url, params, tmpdir)
            if not tgt:
                LOG.warning('Failed download %s', params.get('file'))
                continue
            if xr is None:
                LOG.error('xarray/cfgrib required')
                sys.exit(2)
            ds = xr.open_dataset(tgt, engine='cfgrib')
            try:
                u_name = find_var(ds, ['ugrd', 'u'])
                v_name = find_var(ds, ['vgrd', 'v'])
                if not u_name or not v_name:
                    LOG.warning('UGRD/VGRD vars not found in %s', tgt)
                    continue
                speed_mps, speed_mph, dir_deg, dir_card = get_wind_at_point(ds, u_name, v_name, GORE_COORDS[1], GORE_COORDS[0])

                try:
                    model_run_dt = datetime.strptime(f"{date_ymd}{hour}", '%Y%m%d%H').replace(tzinfo=timezone.utc)
                    dt_valid_utc = model_run_dt + timedelta(hours=int(fhr))
                    dt_local = dt_valid_utc.astimezone(ZoneInfo('America/New_York'))
                    dt_local = dt_local + timedelta(hours=1)
                    date_part = dt_local.strftime('%m/%d/%y')
                    hour_part = dt_local.strftime('%I%p').lstrip('0').lower()
                    valid_time = f"{date_part} {hour_part}"
                except Exception:
                    valid_time = None

                rec = {
                    'forecast_hour': int(fhr),
                    'wind925_speed_mps': speed_mps,
                    'wind925_speed_mph': speed_mph,
                    'wind925_dir_deg': dir_deg,
                    'wind925_dir': dir_card,
                    'valid_time': valid_time,
                    'model_run': f'{date_ymd}T{hour}Z'
                }
                records.append(rec)
            finally:
                ds.close()

        payload = {
            'site': 'Gore Mountain',
            'model_run': f'{date_ymd}T{hour}Z',
            'variable': 'wind_925mb',
            'records': records,
        }
        with open(out_file, 'w') as fh:
            json.dump(payload, fh, indent=2)
        LOG.info('Wrote %d records to %s', len(records), out_file)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == '__main__':
    main()
