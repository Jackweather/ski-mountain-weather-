#!/usr/bin/env python3
"""
Fetch HRRR snow depth (sde) at Gore Mountain and compute cumulative positive-only
snow depth increase across forecast hours. Writes JSON to region northeast.

Output file: region northeast/gore_hrrr_snow_YYYYMMDDtHHz.json

Requirements: requests, xarray, cfgrib, eccodes, numpy
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

LOG = logging.getLogger('hrrr_snow')
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
        'var_SNOD': 'on',
        'lev_surface': 'on',
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


def find_var(ds, keywords):
    for name in ds.data_vars:
        lname = name.lower()
        for kw in keywords:
            if kw in lname:
                return name
    return None


def extract_value_at_point(ds, varname, lat, lon):
    """Extract scalar value from dataset variable at nearest grid point (lat, lon).
    Returns value (float) or None on failure.
    """
    try:
        arr = ds[varname]
        lat_names = [n for n in ds.coords.keys() if 'lat' in n.lower()]
        lon_names = [n for n in ds.coords.keys() if 'lon' in n.lower()]
        if lat_names and lon_names:
            latn = lat_names[0]
            lonn = lon_names[0]
            try:
                pt = arr.sel({latn: lat, lonn: lon}, method='nearest')
                val = float(np.squeeze(pt.values))
            except Exception:
                val = float(np.ravel(arr.values)[0])
        else:
            val = float(np.ravel(arr.values)[0])
        return float(val)
    except Exception:
        return None


def main():
    now = datetime.utcnow()
    date_ymd, hour = None, None
    # probe to find a run that has f00
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

    # ensure full run availability
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
    out_file = os.path.join(out_dir, f'gore_hrrr_snow_{date_ymd}t{hour}z.json')

    records = []
    tmpdir = tempfile.mkdtemp(prefix='hrrrsnow_')
    try:
        run_is_special = hour in SPECIAL_RUNS
        max_f = 48 if run_is_special else 18

        prev_val = None
        cumulative_pos_m = 0.0  # meters

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
                var = find_var(ds, ['sde', 'snow'])
                if not var:
                    LOG.warning('sde variable not found in %s', tgt)
                    continue
                val = extract_value_at_point(ds, var, GORE_COORDS[1], GORE_COORDS[0])
                # sde typically in meters (model dependent). treat as meters.
                if val is None:
                    LOG.warning('Failed to extract sde at point for %s', params.get('file'))
                    continue
                sde_m = float(val)
                sde_in = sde_m * 39.3700787

                if prev_val is None:
                    delta_m = 0.0
                else:
                    delta_m = sde_m - prev_val
                positive_delta_m = float(delta_m) if delta_m > 0 else 0.0
                cumulative_pos_m += positive_delta_m

                # compute valid time
                try:
                    model_run_dt = datetime.strptime(f"{date_ymd}{hour}", '%Y%m%d%H').replace(tzinfo=timezone.utc)
                    dt_valid_utc = model_run_dt + timedelta(hours=int(fhr))
                    dt_local = dt_valid_utc.astimezone(ZoneInfo('America/New_York'))
                    # add one hour to match display convention used elsewhere
                    dt_local = dt_local + timedelta(hours=1)
                    date_part = dt_local.strftime('%m/%d/%y')
                    hour_part = dt_local.strftime('%I%p').lstrip('0').lower()
                    valid_time = f"{date_part} {hour_part}"
                except Exception:
                    valid_time = None

                rec = {
                    'forecast_hour': int(fhr),
                    'sde_m': round(sde_m, 3),
                    'sde_in': round(sde_in, 2),
                    'delta_m': round(delta_m, 3),
                    'delta_in_positive': round(positive_delta_m * 39.3700787, 2),
                    'cumulative_snow_m': round(cumulative_pos_m, 3),
                    'cumulative_snow_in': round(cumulative_pos_m * 39.3700787, 2),
                    'valid_time': valid_time,
                    'model_run': f'{date_ymd}T{hour}Z'
                }
                records.append(rec)

                # set prev_val for next step (use actual sde even if decreased)
                prev_val = sde_m

            finally:
                ds.close()
                # remove the downloaded file to save space
                try:
                    os.remove(tgt)
                except Exception:
                    pass

        payload = {
            'site': 'Gore Mountain',
            'model_run': f'{date_ymd}T{hour}Z',
            'variable': 'sde_m',
            'records': records,
        }
        with open(out_file, 'w') as fh:
            json.dump(payload, fh, indent=2)
        LOG.info('Wrote %d records to %s', len(records), out_file)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == '__main__':
    main()
