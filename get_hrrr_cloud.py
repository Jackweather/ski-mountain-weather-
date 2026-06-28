#!/usr/bin/env python3
"""
Fetch HRRR total cloud cover (TCDC) at Gore Mountain and write JSON categorized by percent.

Output file: region northeast/gore_hrrr_cloud_YYYYMMDDtHHz.json

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

LOG = logging.getLogger('hrrr_cloud')
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
        'var_TCDC': 'on',
        'lev_entire_atmosphere': 'on',
        'var_PRATE': 'on',
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


def find_cloud_var(ds):
    for name in ds.data_vars:
        lname = name.lower()
        if 'tcdc' in lname or 'tcc' in lname or 'cloud' in lname:
            return name
    return list(ds.data_vars.keys())[0]


def find_prate_var(ds):
    for name in ds.data_vars:
        lname = name.lower()
        if 'prate' in lname or 'precip' in lname:
            return name
    return None


def get_cloud_pct(ds, varname, lat, lon):
    arr = ds[varname]
    # attempt nearest point extraction similar to prior helper
    try:
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
    except Exception:
        val = float(np.mean(np.squeeze(arr.values)))

    # interpret units: if between 0-1 -> percent*100, if 0-100 keep
    if val <= 1.0:
        pct = float(val) * 100.0
    else:
        pct = float(val)
    pct = max(0.0, min(100.0, pct))
    return round(pct, 2)


def get_prate_pct(ds, varname, lat, lon):
    arr = ds[varname]
    try:
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
    except Exception:
        val = 0.0

    # PRATE is typically in kg/m2/s (== mm/s). Convert to mm/hr
    mm_per_hr = float(val) * 3600.0

    # Map mm/hr to a 0-100% precipitation "chance" by linear scaling
    # cap_mm_per_hr defines the value that maps to 100% (heavy precipitation)
    cap_mm_per_hr = 5.0
    pct = min(100.0, (mm_per_hr / cap_mm_per_hr) * 100.0)
    pct = max(0.0, pct)
    return round(pct, 2), round(mm_per_hr, 3)


def precip_category_from_mm(mm_per_hr):
    """Map mm/hr to a simple category: Light, Steady, Heavy (or None)."""
    if mm_per_hr <= 0.0:
        return 'No Rain'
    # light: up to 0.2 mm/hr, steady: up to 2 mm/hr, heavy: above 2 mm/hr
    if mm_per_hr <= 0.2:
        return 'Light Rain'
    if mm_per_hr <= 2.0:
        return 'Steady Rain'
    return 'Heavy Rain'


def categorize(pct):
    if pct <= 10:
        return ('Sunny', '☀️')
    if pct <= 40:
        return ('Partly Cloudy', '⛅')
    if pct <= 75:
        return ('Mostly Cloudy', '🌥️')
    return ('Cloudy', '☁️')


def main():
    # find a run where f00 exists (fast probe)
    now = datetime.utcnow()
    date_ymd, hour = None, None
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

    # Ensure the full set of forecast hour files are available for the chosen run.
    # If any forecast hour is missing, fall back one run and retry (up to max_fallbacks).
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

        # fallback to previous run (one hour earlier)
        LOG.info('Falling back one run from %sT%sZ', chosen_date, chosen_hour)
        dt = datetime.strptime(f"%s%s" % (chosen_date, chosen_hour), '%Y%m%d%H') - timedelta(hours=1)
        chosen_date = dt.strftime('%Y%m%d')
        chosen_hour = dt.strftime('%H')
        attempt += 1

    if attempt >= max_fallbacks and missing:
        LOG.error('No complete run found after %d fallbacks', max_fallbacks)
        sys.exit(1)

    out_dir = os.path.join(os.getcwd(), 'region northeast')
    # clear output directory but keep any files for the selected run
    keep_token = f"{date_ymd}t{hour}z"
    if os.path.isdir(out_dir):
        try:
            for fn in os.listdir(out_dir):
                if keep_token in fn:
                    continue
                path = os.path.join(out_dir, fn)
                if os.path.isfile(path):
                    os.remove(path)
                elif os.path.isdir(path):
                    shutil.rmtree(path, ignore_errors=True)
        except Exception:
            LOG.warning('Failed to clear output directory %s', out_dir)
    else:
        os.makedirs(out_dir, exist_ok=True)
    out_file = os.path.join(out_dir, f'gore_hrrr_cloud_{date_ymd}t{hour}z.json')

    records = []
    tmpdir = tempfile.mkdtemp(prefix='hrrrcloud_')
    try:
        for fhr in range(0, max_f+1):
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
                var = find_cloud_var(ds)
                pct = get_cloud_pct(ds, var, GORE_COORDS[1], GORE_COORDS[0])
                cat, sym = categorize(pct)

                # try to find precipitation rate and compute a precip percent
                prate_var = find_prate_var(ds)
                precip_pct = 0.0
                precip_mm_hr = 0.0
                precip_sym = ''
                if prate_var:
                    try:
                        precip_pct, precip_mm_hr = get_prate_pct(ds, prate_var, GORE_COORDS[1], GORE_COORDS[0])
                        if precip_pct > 0:
                            precip_sym = '🌧️'
                    except Exception:
                        precip_pct = 0.0
                        precip_mm_hr = 0.0
                        precip_sym = ''

                # determine a simple category for precipitation intensity
                precip_category = precip_category_from_mm(precip_mm_hr)

                # combine symbols: append rain if precip chance > 30%
                combined_sym = sym
                if precip_pct > 30:
                    combined_sym = f"{sym} {precip_sym}"

                # compute valid time using the same method as Hrrr_Temp.py
                try:
                    model_run_dt = datetime.strptime(f"{date_ymd}{hour}", '%Y%m%d%H').replace(tzinfo=timezone.utc)
                    dt_valid_utc = model_run_dt + timedelta(hours=int(fhr))
                    dt_local = dt_valid_utc.astimezone(ZoneInfo('America/New_York'))
                    # add one hour to match requested display (e.g., 19Z -> 4pm)
                    dt_local = dt_local + timedelta(hours=1)
                    date_part = dt_local.strftime('%m/%d/%y')
                    hour_part = dt_local.strftime('%I%p').lstrip('0').lower()
                    valid_time = f"{date_part} {hour_part}"
                except Exception:
                    valid_time = None

                rec = {
                    'forecast_hour': int(fhr),
                    'cloud_percent': pct,
                    'category': cat,
                    'symbol': sym,
                    'precip_percent': precip_pct,
                    'precip_mm_hr': precip_mm_hr,
                    'precip_category': precip_category,
                    'precip_symbol': precip_sym,
                    'combined_symbol': combined_sym,
                    'valid_time': valid_time,
                    'model_run': f'{date_ymd}T{hour}Z'
                }
                records.append(rec)
            finally:
                ds.close()

        payload = {
            'site': 'Gore Mountain',
            'model_run': f'{date_ymd}T{hour}Z',
            'variable': 'total_cloud_cover_pct',
            'records': records,
        }
        with open(out_file, 'w') as fh:
            json.dump(payload, fh, indent=2)
        LOG.info('Wrote %d records to %s', len(records), out_file)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == '__main__':
    main()
