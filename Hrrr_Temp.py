#!/usr/bin/env python3
"""
Fetch HRRR 2-m temperature for a Northeast bounding box for the latest available run.

Requirements (recommended):
  conda install -c conda-forge xarray cfgrib eccodes requests numpy

Usage:
  python get_hrrr_ne.py

Output:
  Creates directory `region northeast` and writes a JSON file named
  like `hrrr_YYYYMMDDtHHz_region_northeast.json` containing an array of
  objects: {forecast_hour, temp_f, valid_time, model_run}
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

LOG = logging.getLogger('hrrr_fetch')
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

# Northeast bounding box (approx): leftlon, rightlon, toplat, bottomlat
NE_BBOX = dict(leftlon=-82, rightlon=-66, toplat=47, bottomlat=38)

# Gore Mountain coords (lon, lat)
GORE_COORDS = (-74.0069444, 43.6722222)

BASE_URL = 'https://nomads.ncep.noaa.gov/cgi-bin/filter_hrrr_2d.pl'

# HTTP headers to use when contacting nomads
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) '
                  'Chrome/114.0.0.0 Safari/537.36'
}

SPECIAL_RUNS = {'00', '06', '12', '18'}

def build_filter_url(date_ymd, hour_str, fhr, bbox=NE_BBOX):
    # date_ymd: YYYYMMDD, hour_str: 'HH', fhr: int
    # forecast hour string without leading 'f' (nomads expects 'wrfsfcf00', not 'wrfsfcff00')
    ffs = f"{fhr:02d}"
    dir_path = f"/hrrr.{date_ymd}/conus"
    filename = f"hrrr.t{hour_str}z.wrfsfcf{ffs}.grib2"
    params = {
        'dir': dir_path,
        'file': filename,
        'var_TMP': 'on',
        'lev_2_m_above_ground': 'on',
        'leftlon': str(bbox['leftlon']),
        'rightlon': str(bbox['rightlon']),
        'toplat': str(bbox['toplat']),
        'bottomlat': str(bbox['bottomlat']),
    }
    return BASE_URL, params


def download_file(url, params, target_dir, timeout=120):
    """Download the filtered GRIB file to target_dir; return path or None."""
    file_name = params.get('file')
    if not file_name:
        return None
    target_path = os.path.join(target_dir, file_name)
    try:
        r = requests.get(url, params=params, stream=True, timeout=timeout, headers=HEADERS)
        if r.status_code != 200:
            LOG.debug('Download failed status=%s url=%s', r.status_code, r.url)
            return None
        with open(target_path, 'wb') as fh:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    fh.write(chunk)
        if os.path.getsize(target_path) < 10240:
            LOG.warning('%s too small; deleting', target_path)
            os.remove(target_path)
            return None
        return target_path
    except Exception as e:
        LOG.debug('Download exception for %s: %s', params.get('file'), e)
        return None


def url_exists(url, params, timeout=10):
    try:
        # Some servers (including nomads) do not respond to HEAD as expected.
        # Use GET with stream and read a small chunk to verify availability.
        r = requests.get(url, params=params, stream=True, timeout=timeout, headers=HEADERS)
        LOG.debug('Probe URL: %s (status %s)', r.url, r.status_code)
        if r.status_code != 200:
            # log a snippet of the response body (HTML) for diagnostics
            try:
                txt = r.text[:400]
            except Exception:
                txt = '<no-text>'
            LOG.debug('Probe failed status=%s body_snippet=%s', r.status_code, txt)
            r.close()
            return False
        # Try reading a tiny amount to ensure content is present
        try:
            chunk = r.raw.read(64)
            return bool(chunk)
        finally:
            r.close()
    except Exception as e:
        LOG.debug('Probe exception: %s', e)
        return False


def find_temp_variable(ds):
    for name in ds.data_vars:
        lname = name.lower()
        if 'temp' in lname or lname == 't' or 'tmp' in lname:
            return name
    return list(ds.data_vars.keys())[0]


def get_var_at_location(ds, varname, lat_pt, lon_pt):
    # Attempt to find lat/lon coordinate variable names
    lat_names = [n for n in ds.coords.keys() if 'lat' in n.lower()]
    lon_names = [n for n in ds.coords.keys() if 'lon' in n.lower()]
    if not lat_names or not lon_names:
        # try common CF names
        lat_names = [n for n in ds.coords.keys() if 'y' == n.lower() or 'latitude' in n.lower()]
        lon_names = [n for n in ds.coords.keys() if 'x' == n.lower() or 'longitude' in n.lower()]

    var = ds[varname]
    arr = np.squeeze(var.values)

    # choose lat/lon arrays
    if lat_names and lon_names:
        latn = lat_names[0]
        lonn = lon_names[0]
        lats = ds[latn].values
        lons = ds[lonn].values
        # normalize lons to -180..180
        lons = np.where(lons > 180, lons - 360, lons)

        # 2D lat/lon
        if lats.ndim == 2 and lons.ndim == 2:
            dist = (lats - lat_pt)**2 + (lons - lon_pt)**2
            iy, ix = np.unravel_index(np.argmin(dist), dist.shape)
            if arr.ndim == 2:
                return float(arr[iy, ix])
            # handle extra leading dims
            return float(arr[..., iy, ix])

        # 1D lat/lon
        if lats.ndim == 1 and lons.ndim == 1:
            iy = int(np.abs(lats - lat_pt).argmin())
            ix = int(np.abs(lons - lon_pt).argmin())
            if arr.ndim == 2:
                return float(arr[iy, ix])
            if arr.ndim >= 3:
                return float(arr[..., iy, ix])
            return float(arr[iy])

    # fallback: global mean
    return float(np.mean(arr))


def download_filter_grib(url, params, target_path, timeout=60):
    r = requests.get(url, params=params, stream=True, timeout=timeout)
    r.raise_for_status()
    with open(target_path, 'wb') as fh:
        for chunk in r.iter_content(chunk_size=8192):
            if chunk:
                fh.write(chunk)


def find_latest_run(max_lookback_hours=72):
    now = datetime.utcnow()
    for dh in range(0, max_lookback_hours + 1):
        t = now - timedelta(hours=dh)
        date_ymd = t.strftime('%Y%m%d')
        hour = t.strftime('%H')
        # check if f00 exists for this run
        url, params = build_filter_url(date_ymd, hour, 0)
        LOG.debug('Probing run %sT%sZ -> file %s', date_ymd, hour, params.get('file'))
        if url_exists(url, params):
            LOG.info(f'Found available run: {date_ymd}T{hour}Z')
            return date_ymd, hour
    return None, None


def read_point_temp_f(grib_path, lon, lat):
    if xr is None:
        raise RuntimeError('xarray/cfgrib not available. Install cfgrib and eccodes.')
    ds = xr.open_dataset(grib_path, engine='cfgrib')
    try:
        data_vars = list(ds.data_vars.keys())
        if not data_vars:
            raise RuntimeError('No data variables found in GRIB dataset')
        var = data_vars[0]
        arr = ds[var]

        # find coordinate names for lat/lon
        lat_names = [n for n in ds.coords.keys() if 'lat' in n.lower()]
        lon_names = [n for n in ds.coords.keys() if 'lon' in n.lower()]

        if lat_names and lon_names:
            lat_name = lat_names[0]
            lon_name = lon_names[0]
            try:
                # use nearest neighbor selection if coords are 1D
                pt = arr.sel({lat_name: lat, lon_name: lon}, method='nearest')
                val = float(pt.values)
            except Exception:
                # fallback: compute distance on 2D coords
                lat2d = ds[lat_name].values
                lon2d = ds[lon_name].values
                # flatten and find nearest index
                flat_lat = np.ravel(lat2d)
                flat_lon = np.ravel(lon2d)
                dist = (flat_lat - lat)**2 + (flat_lon - lon)**2
                idx = int(np.argmin(dist))
                # map flattened index back to arr grid
                # assume arr has shape (y, x) or similar
                flat_vals = np.ravel(arr.values)
                val = float(flat_vals[idx])
        else:
            # no lat/lon coords found; fallback to global mean
            val = float(arr.mean().values)

        units = ds[var].attrs.get('units', '').lower()
        # convert to Fahrenheit
        if 'k' in units or 'kelvin' in units:
            c = val - 273.15
            f = c * 9.0/5.0 + 32.0
        elif 'c' in units or 'degc' in units or 'celsius' in units:
            f = val * 9.0/5.0 + 32.0
        else:
            # assume Kelvin if unknown
            f = (val - 273.15) * 9.0/5.0 + 32.0

        # try to get time coordinate
        valid_time = None
        if 'time' in ds.coords:
            try:
                t = ds['time'].values
                valid_time = str(np.asarray(t).tolist())
            except Exception:
                valid_time = None
        return round(float(f), 2), valid_time
    finally:
        ds.close()


def main():
    date_ymd, hour = find_latest_run()
    if not date_ymd:
        LOG.error('No run found in lookback window')
        sys.exit(1)

    # If any forecast file for the chosen run is missing, fall back one run and retry.
    max_fallbacks = 6
    attempt = 0
    chosen_date = date_ymd
    chosen_hour = hour

    while attempt < max_fallbacks:
        run_is_special = chosen_hour in SPECIAL_RUNS
        max_f = 48 if run_is_special else 18

        # Probe all forecast hours for this candidate run
        missing = False
        for fhr in range(0, max_f + 1):
            url, params = build_filter_url(chosen_date, chosen_hour, fhr)
            if not url_exists(url, params):
                LOG.warning('Probe missing for run %sT%sZ file %s', chosen_date, chosen_hour, params.get('file'))
                missing = True
                break

        if not missing:
            # all files present for this run
            date_ymd, hour = chosen_date, chosen_hour
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

    # now date_ymd/hour are the selected run
    run_is_special = hour in SPECIAL_RUNS
    max_f = 48 if run_is_special else 18

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

    out_file = os.path.join(out_dir, f'gore_hrrr_temp_{date_ymd}t{hour}z.json')
    records = []

    tmpdir = tempfile.mkdtemp(prefix='hrrr_')
    try:
        for fhr in range(0, max_f + 1):
            url, params = build_filter_url(date_ymd, hour, fhr)
            LOG.info('Processing %s', params.get('file'))

            if not url_exists(url, params):
                LOG.warning('File not available: %s - skipping', params.get('file'))
                continue

            tgt = download_file(url, params, tmpdir)
            if not tgt:
                LOG.warning('Failed to download %s', params.get('file'))
                continue

            # open and extract temperature at Gore coords
            try:
                if xr is None:
                    LOG.error('xarray/cfgrib not available; cannot read GRIB files')
                    sys.exit(2)
                ds = xr.open_dataset(tgt, engine='cfgrib')
                try:
                    varname = find_temp_variable(ds)
                    raw_val = get_var_at_location(ds, varname, GORE_COORDS[1], GORE_COORDS[0])
                    units = ds[varname].attrs.get('units', '').lower()
                    # convert
                    if 'k' in units or 'kelvin' in units:
                        temp_c = float(raw_val) - 273.15
                        temp_f = temp_c * 9.0/5.0 + 32.0
                    elif 'c' in units or 'degc' in units or 'celsius' in units:
                        temp_f = float(raw_val) * 9.0/5.0 + 32.0
                    else:
                        temp_f = (float(raw_val) - 273.15) * 9.0/5.0 + 32.0

                    # compute valid time as model_run + forecast hour, converted to America/New_York
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

                    record = {
                        'forecast_hour': int(fhr),
                        'temp_f': round(float(temp_f), 2),
                        'valid_time': valid_time,
                        'model_run': f'{date_ymd}T{hour}Z'
                    }
                    records.append(record)
                    LOG.info('f%02d -> %s F', fhr, record['temp_f'])
                finally:
                    ds.close()
            except Exception:
                LOG.exception('Error reading GRIB %s', tgt)

        # write JSON with top-level metadata
        payload = {
            'site': 'Gore Mountain',
            'model_run': f'{date_ymd}T{hour}Z',
            'variable': '2m_temperature_F',
            'records': records,
        }
        with open(out_file, 'w') as fh:
            json.dump(payload, fh, indent=2)
        LOG.info('Wrote %d records to %s', len(records), out_file)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == '__main__':
    main()
