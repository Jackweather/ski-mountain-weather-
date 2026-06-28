from flask import Flask, render_template, jsonify
import os
import glob
import json

app = Flask(__name__)

DEFAULT_MAPBOX_TOKEN = "pk.eyJ1Ijoid2VhdGhlcmphY2sxODkiLCJhIjoiY21tYzN0MHVrMDI4djJxcHdzNXdpOTQ2MyJ9.IM4BBEnM5tNLI2SnEyl3uw"


@app.route("/")
def index():
    return render_template("index.html", mapbox_token=DEFAULT_MAPBOX_TOKEN)


@app.route('/api/gore')
def api_gore():
    """Return the most recent Gore JSON (gore_hrrr_temp_*.json) from the `region northeast` folder."""
    data_dir = os.path.join(os.getcwd(), 'region northeast')
    if not os.path.isdir(data_dir):
        return jsonify({'error': 'no data directory'}), 404
    files = glob.glob(os.path.join(data_dir, 'gore_hrrr_temp_*.json'))
    if not files:
        return jsonify({'error': 'no gore data found'}), 404
    latest = sorted(files)[-1]
    try:
        with open(latest, 'r') as fh:
            data = json.load(fh)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    # also try to include latest cloud JSON if present
    cloud_files = glob.glob(os.path.join(data_dir, 'gore_hrrr_cloud_*.json'))
    cloud_payload = None
    if cloud_files:
        try:
            cf = sorted(cloud_files)[-1]
            with open(cf, 'r') as ch:
                cloud_payload = json.load(ch)
        except Exception:
            cloud_payload = None

    # also try to include latest surface wind JSON if present (prefer non-925mb files)
    wind_files = glob.glob(os.path.join(data_dir, 'gore_hrrr_wind_*.json'))
    wind_payload = None
    if wind_files:
        try:
            # prefer files that do not include '925' or '925mb' in the filename (surface winds)
            non925 = [p for p in wind_files if '925' not in os.path.basename(p)]
            chosen = sorted(non925)[-1] if non925 else sorted(wind_files)[-1]
            with open(chosen, 'r') as wh:
                wind_payload = json.load(wh)
        except Exception:
            wind_payload = None

    # also try to include latest snow JSON if present
    snow_files = glob.glob(os.path.join(data_dir, 'gore_hrrr_snow_*.json'))
    snow_payload = None
    if snow_files:
        try:
            sf = sorted(snow_files)[-1]
            with open(sf, 'r') as sh:
                snow_payload = json.load(sh)
        except Exception:
            snow_payload = None

    # also try to include latest visibility JSON if present
    vis_files = glob.glob(os.path.join(data_dir, 'gore_hrrr_vis_*.json'))
    vis_payload = None
    if vis_files:
        try:
            vf = sorted(vis_files)[-1]
            with open(vf, 'r') as vh:
                vis_payload = json.load(vh)
        except Exception:
            vis_payload = None

    # also try to include latest 925mb wind JSON if present
    wind925_files = glob.glob(os.path.join(data_dir, 'gore_hrrr_wind_925mb_*.json'))
    wind925_payload = None
    if wind925_files:
        try:
            w5 = sorted(wind925_files)[-1]
            with open(w5, 'r') as wh5:
                wind925_payload = json.load(wh5)
        except Exception:
            wind925_payload = None

    return jsonify({'file': os.path.basename(latest), 'payload': data, 'cloud': cloud_payload, 'wind': wind_payload, 'snow': snow_payload, 'vis': vis_payload, 'wind925': wind925_payload})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
