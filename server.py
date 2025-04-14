from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
import pandas as pd
import math

app = Flask(__name__)
CORS(app)

# Load Excel Data
df = pd.read_excel("Mango_Data.xlsx")

# Cleanup + Compute Min/Max Frequency
df["Latitude"] = df["Latitude"].round(6)
df["Longitude"] = df["Longitude"].round(6)
df["Bandwidth_MHz"] = df["Bandwidth_kHz"] / 1000.0
df["MinFrequency"] = df["Frequency"] - (df["Bandwidth_MHz"] / 2)
df["MaxFrequency"] = df["Frequency"] + (df["Bandwidth_MHz"] / 2)

def get_distance(lat1, lon1, lat2, lon2):
    R = 6371  # Earth radius
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/getData", methods=["GET"])
def get_data():
    lat = float(request.args.get("latitude"))
    lon = float(request.args.get("longitude"))
    radius = float(request.args.get("radius"))
    min_freq = float(request.args.get("min_freq"))
    max_freq = float(request.args.get("max_freq"))

    filtered = df[
        (df["MaxFrequency"] >= min_freq) &
        (df["MinFrequency"] <= max_freq)
    ].copy()

    filtered["Distance"] = filtered.apply(
        lambda row: get_distance(lat, lon, row["Latitude"], row["Longitude"]),
        axis=1
    )

    filtered = filtered[filtered["Distance"] <= radius]

    print(f"✅ Filtered rows returned: {len(filtered)}")
    return jsonify(filtered.to_dict(orient="records"))

if __name__ == "__main__":
    app.run(debug=True)