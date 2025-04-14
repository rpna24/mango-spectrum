# mango_app.py

import pandas as pd
import math
from dash import Dash, dcc, html, Input, Output
import plotly.graph_objects as go

# Load data from Excel
df = pd.read_excel("Mango_Data.xlsx")

# Preprocess data
df["Latitude"] = df["Latitude"].round(6)
df["Longitude"] = df["Longitude"].round(6)
df["Bandwidth_MHz"] = df["Bandwidth_kHz"] / 1000.0
df["MinFrequency"] = df["Frequency"] - (df["Bandwidth_MHz"] / 2)
df["MaxFrequency"] = df["Frequency"] + (df["Bandwidth_MHz"] / 2)

# Distance Calculation
def get_distance(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * \
        math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

# Create Dash App
app = Dash(__name__)
app.title = "Mango Spectrum Viewer"

app.layout = html.Div([
    html.H1("📡 Mango Frequency Spectrum Viewer", style={'textAlign': 'center'}),
    
    html.Div([
        html.Label("Latitude (- South):"), 
        dcc.Input(id='latitude', type='number', value=-28.3, step=0.000001),

        html.Label("Longitude (+ East):"),
        dcc.Input(id='longitude', type='number', value=153.5, step=0.000001),

        html.Label("Radius (km):"),
        dcc.Input(id='radius', type='number', value=100),

        html.Label("Min Frequency (MHz):"),
        dcc.Input(id='min_freq', type='number', value=1000),

        html.Label("Max Frequency (MHz):"),
        dcc.Input(id='max_freq', type='number', value=90000),
    ], style={'display': 'flex', 'gap': '20px', 'flexWrap': 'wrap', 'marginBottom': '30px'}),
    
    dcc.Graph(id='spectrum-plot')
])

@app.callback(
    Output('spectrum-plot', 'figure'),
    Input('latitude', 'value'),
    Input('longitude', 'value'),
    Input('radius', 'value'),
    Input('min_freq', 'value'),
    Input('max_freq', 'value')
)
def update_plot(lat, lon, radius, min_freq, max_freq):
    if None in [lat, lon, radius, min_freq, max_freq]:
        return go.Figure()

    # Apply frequency filter
    filtered = df[
        (df["MaxFrequency"] >= min_freq) &
        (df["MinFrequency"] <= max_freq)
    ].copy()

    # Apply spatial filter
    filtered["Distance"] = filtered.apply(
        lambda row: get_distance(lat, lon, row["Latitude"], row["Longitude"]),
        axis=1
    )
    filtered = filtered[filtered["Distance"] <= radius]

    if filtered.empty:
        return go.Figure(layout={"title": "No data found for selected range"})

    # Grouped by Device_Type
    traces = []
    colors = {'T': '#0072ff', 'R': '#28a745'}

    for device_type in filtered["Device_Type"].unique():
        subset = filtered[filtered["Device_Type"] == device_type]
        traces.append(go.Bar(
            y=[device_type] * len(subset),
            x=(subset["MaxFrequency"] - subset["MinFrequency"]),
            base=subset["MinFrequency"],
            orientation='h',
            marker=dict(color=colors.get(device_type, "#999")),
            text=[f'{row.Frequency} MHz, {row.Bandwidth_kHz} kHz' for _, row in subset.iterrows()],
            hoverinfo='text',
            name=f"{'Transmitter' if device_type == 'T' else 'Receiver'}"
        ))

    layout = go.Layout(
        title="Filtered Frequency Spectrum",
        barmode='stack',
        xaxis=dict(title="Frequency (MHz)"),
        yaxis=dict(title="Device Type"),
        height=600
    )

    return go.Figure(data=traces, layout=layout)

if __name__ == '__main__':
    app.run(debug=True)

