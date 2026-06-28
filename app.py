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

    # also try to include latest wind JSON if present
    wind_files = glob.glob(os.path.join(data_dir, 'gore_hrrr_wind_*.json'))
    wind_payload = None
    if wind_files:
        try:
            wf = sorted(wind_files)[-1]
            with open(wf, 'r') as wh:
                wind_payload = json.load(wh)
        except Exception:
            wind_payload = None

    return jsonify({'file': os.path.basename(latest), 'payload': data, 'cloud': cloud_payload, 'wind': wind_payload})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
