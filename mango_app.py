import pandas as pd
import math
from dash import Dash, dcc, html, Input, Output
import plotly.graph_objects as go

app = Dash(__name__)
server = app.server  # 👈 REQUIRED FOR GUNICORN

# Load Excel
df = pd.read_excel("Mango_Data.xlsx")
df["Latitude"] = df["Latitude"].round(6)
df["Longitude"] = df["Longitude"].round(6)
df["Bandwidth_MHz"] = df["Bandwidth_kHz"] / 1000.0

# Split and Tag
tx_df = df[df["Tx_Frequency"].notnull()].copy()
tx_df["Frequency"] = tx_df["Tx_Frequency"]
tx_df["Device_Type"] = "T"

rx_df = df[df["Rx_Frequency"].notnull()].copy()
rx_df["Frequency"] = rx_df["Rx_Frequency"]
rx_df["Device_Type"] = "R"

df_combined = pd.concat([tx_df, rx_df], ignore_index=True)
df_combined["MinFrequency"] = df_combined["Frequency"] - (df_combined["Bandwidth_MHz"] / 2)
df_combined["MaxFrequency"] = df_combined["Frequency"] + (df_combined["Bandwidth_MHz"] / 2)

def get_distance(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * \
        math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

# Dash App
app = Dash(__name__)
app.title = "Mango Frequency Spectrum Viewer"

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

    dcc.Graph(id='spectrum-plot'),

    html.Div(id='click-output', style={
        'marginTop': '20px',
        'padding': '10px',
        'border': '1px solid #ccc',
        'borderRadius': '6px',
        'backgroundColor': '#f9f9f9',
        'fontSize': '16px',
        'display': 'none'
    })
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

    filtered = df_combined[
        (df_combined["MaxFrequency"] >= min_freq) &
        (df_combined["MinFrequency"] <= max_freq)
    ].copy()

    filtered["Distance"] = filtered.apply(
        lambda row: get_distance(lat, lon, row["Latitude"], row["Longitude"]),
        axis=1
    )
    filtered = filtered[filtered["Distance"] <= radius]

    if filtered.empty:
        return go.Figure(
            layout=go.Layout(
                title="No Data Found Within Range",
                xaxis=dict(title="Frequency (MHz)"),
                yaxis=dict(title="Device Type", type='category'),
                height=600
            )
        )

    dynamic_min = max(min(filtered["MinFrequency"]) - 10, 0)
    dynamic_max = max(filtered["MaxFrequency"]) + 10
    filtered["Tier"] = filtered.groupby(["Device_Type", "Frequency", "Bandwidth_kHz"]).ngroup()

    # Background
    background_traces = []
    for device in ['T', 'R']:
        background_traces.append(go.Bar(
            y=[f"{device}_base"],
            x=[dynamic_max - dynamic_min],
            base=dynamic_min,
            orientation='h',
            marker=dict(color='lightgrey'),
            hoverinfo='skip',
            showlegend=False,
            opacity=0.3
        ))

    overlay_traces = []
    colors = {'T': '#0072ff', 'R': '#28a745'}

    for i, row in filtered.iterrows():
        overlay_traces.append(go.Bar(
            y=[f"{row.Device_Type}_Tier_{row.Tier}"],
            x=[row.MaxFrequency - row.MinFrequency],
            base=row.MinFrequency,
            orientation='h',
            marker=dict(color=colors[row.Device_Type], opacity=0.9),
            name="Transmitter" if row.Device_Type == 'T' else "Receiver",
            text=(
                f"{row.Frequency} MHz<br>"
                f"{row.Bandwidth_kHz} kHz<br>"
                f"Lat: {row.Latitude}, Lon: {row.Longitude}"
            ),
            hoverinfo='text',
            customdata=[[
                row.Licence_No,
                row.Site_ID,
                row.Latitude,
                row.Longitude
            ]],
            showlegend=False
        ))

    layout = go.Layout(
        title="Filtered Frequency Spectrum (Click to Reveal Details)",
        barmode='overlay',
        xaxis=dict(title="Frequency (MHz)", range=[dynamic_min, dynamic_max]),
        yaxis=dict(title="Device Type + Tier", type='category'),
        height=700
    )

    return go.Figure(data=background_traces + overlay_traces, layout=layout)

# 🔍 Display Licence, Site, Coordinates on double-click
@app.callback(
    Output('click-output', 'children'),
    Output('click-output', 'style'),
    Input('spectrum-plot', 'clickData')
)
def show_click_data(clickData):
    if not clickData or 'points' not in clickData:
        return "", {'display': 'none'}

    point = clickData['points'][0]
    licence_no, site_id, lat, lon = point['customdata']

    return (
        html.Div([
            html.P(f"📄 Licence No: {licence_no}"),
            html.P(f"🏢 Site ID: {site_id}"),
            html.P(f"📍 Coordinates: Latitude {lat}, Longitude {lon}")
        ]),
        {'display': 'block', 'marginTop': '20px', 'padding': '10px',
         'border': '1px solid #ccc', 'borderRadius': '6px',
         'backgroundColor': '#f9f9f9', 'fontSize': '16px'}
    )

import os

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8050))
    app.run(host="0.0.0.0", port=port, debug=False)
    


# if __name__ == '__main__':
#     app.run(debug=True)
