import pandas as pd
import math
from dash import Dash, dcc, html, Input, Output, State
import plotly.graph_objects as go
import plotly.express as px
import os
import webbrowser

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
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

# App Initialization
app = Dash(__name__)
server = app.server
app.title = "Mango Frequency Spectrum Viewer"

# Theme Colors
theme = {
    "background": "#660033",
    "plot_bgcolor": "#191970",
    "plot_border": "#000099",
    "font_color": "white"
}

input_style = {
    'padding': '10px',
    'margin': '5px',
    'width': '120px',
    'borderRadius': '6px',
    'border': '1px solid #999',
    'backgroundColor': '#eaeaea',
    'color': 'black'
}

button_style = {
    'padding': '12px 20px',
    'marginLeft': '10px',
    'backgroundColor': '#28a745',
    'border': 'none',
    'color': 'white',
    'borderRadius': '6px',
    'cursor': 'pointer',
    'fontWeight': 'bold',
    'boxShadow': '0 4px 8px rgba(0,0,0,0.3)'
}

app.layout = html.Div([
    dcc.Store(id='submit-trigger', data=0),

    html.H1("📡 Mango Frequency Spectrum Viewer", id="title", style={'textAlign': 'center', 'color': theme["font_color"]}),

    html.Div([
        html.Div([
            html.Label("Latitude (- South):", style={'color': theme["font_color"]}),
            dcc.Input(id='latitude', type='number', value=-28.3, step=0.000001, style=input_style),
        ]),
        html.Div([
            html.Label("Longitude (+ East):", style={'color': theme["font_color"]}),
            dcc.Input(id='longitude', type='number', value=153.5, step=0.000001, style=input_style),
        ]),
        html.Div([
            html.Label("Radius (km):", style={'color': theme["font_color"]}),
            dcc.Input(id='radius', type='number', value=100, style=input_style),
        ]),
        html.Div([
            html.Label("Min Frequency (MHz):", style={'color': theme["font_color"]}),
            dcc.Input(id='min_freq', type='number', value=6000, style=input_style),
        ]),
        html.Div([
            html.Label("Max Frequency (MHz):", style={'color': theme["font_color"]}),
            dcc.Input(id='max_freq', type='number', value=8000, style=input_style),
        ]),
        html.Button('🔎 Enter', id='submit-button', n_clicks=0, style=button_style),
        html.Button('🗺️ Plot Nearby Points', id='map-button', n_clicks=0, style=button_style),
    ], style={'display': 'flex', 'gap': '15px', 'flexWrap': 'wrap', 'justifyContent': 'center', 'marginBottom': '30px'}),

    html.Div([
        dcc.Graph(id='spectrum-plot', style={'border': f'6px solid {theme["plot_border"]}'})
    ]),

    html.Div(id='click-output', style={
        'marginTop': '20px',
        'padding': '20px',
        'borderRadius': '10px',
        'backgroundColor': theme["plot_border"],
        'color': 'white',
        'fontSize': '18px',
        'display': 'none'
    }),

    dcc.Input(id='hidden-enter', type='text', style={'display': 'none'})
], style={'background': theme["background"], 'minHeight': '100vh', 'padding': '30px'})

@app.callback(
    Output('submit-trigger', 'data'),
    Input('submit-button', 'n_clicks'),
    Input('hidden-enter', 'n_submit')
)
def trigger_submit(n_clicks, n_submit):
    return (n_clicks or 0) + (n_submit or 0)

@app.callback(
    Output('spectrum-plot', 'figure'),
    Input('submit-trigger', 'data'),
    State('latitude', 'value'),
    State('longitude', 'value'),
    State('radius', 'value'),
    State('min_freq', 'value'),
    State('max_freq', 'value')
)
def update_plot(n, lat, lon, radius, min_freq, max_freq):
    if None in [lat, lon, radius, min_freq, max_freq]:
        return go.Figure()

    filtered = df_combined[
        (df_combined["MaxFrequency"] >= min_freq) &
        (df_combined["MinFrequency"] <= max_freq)
    ].copy()

    filtered["Distance"] = filtered.apply(lambda row: get_distance(lat, lon, row["Latitude"], row["Longitude"]), axis=1)
    filtered = filtered[filtered["Distance"] <= radius]

    dynamic_min = max(min(filtered["MinFrequency"]) - 10, 0)
    dynamic_max = max(filtered["MaxFrequency"]) + 10
    filtered["Tier"] = filtered.groupby(["Device_Type", "Frequency", "Bandwidth_kHz"]).ngroup()

    overlay_traces = []
    colors = {'T': '#007bff', 'R': '#28a745'}

    for _, row in filtered.iterrows():
        overlay_traces.append(go.Bar(
            y=[f"{row.Device_Type}_Tier_{row.Tier}"],
            x=[row.MaxFrequency - row.MinFrequency],
            base=row.MinFrequency,
            orientation='h',
            marker=dict(color=colors[row.Device_Type], opacity=0.9),
            hoverinfo='text',
            customdata=[[row.Frequency, row.Bandwidth_MHz, row.Latitude, row.Longitude, row.Licence_No, row.Site_ID]],
            hovertemplate="<b>Freq:</b> %{customdata[0]} MHz<br><b>BW:</b> %{customdata[1]} MHz<br><b>Lat:</b> %{customdata[2]}<br><b>Lon:</b> %{customdata[3]}<extra></extra>",
            showlegend=False
        ))

    layout = go.Layout(
        title="Filtered Frequency Spectrum (Click bars to view details)",
        barmode='overlay',
        xaxis=dict(title="Frequency (MHz)", range=[dynamic_min, dynamic_max]),
        yaxis=dict(showticklabels=False),
        height=700,
        plot_bgcolor=theme["plot_bgcolor"]
    )

    return go.Figure(data=overlay_traces, layout=layout)

@app.callback(
    Output('click-output', 'children'),
    Output('click-output', 'style'),
    Input('spectrum-plot', 'clickData')
)
def display_click_info(clickData):
    if not clickData or 'points' not in clickData:
        return "", {'display': 'none'}

    point = clickData['points'][0]
    frequency, bandwidth, lat, lon, licence_no, site_id = point['customdata']

    return (
        html.Div([
            html.H3("📋 Details:"),
            html.P(f"Licence No: {licence_no}"),
            html.P(f"Site ID: {site_id}"),
            html.P(f"Latitude: {lat}"),
            html.P(f"Longitude: {lon}"),
            html.P(f"Frequency: {frequency} MHz"),
            html.P(f"Bandwidth: {bandwidth} MHz"),
        ]),
        {'display': 'block',
         'marginTop': '20px',
         'padding': '20px',
         'borderRadius': '10px',
         'backgroundColor': theme["plot_border"],
         'color': 'white',
         'fontSize': '18px'}
    )

@app.callback(
    Output('hidden-enter', 'value'),
    Input('map-button', 'n_clicks'),
    State('latitude', 'value'),
    State('longitude', 'value'),
    State('radius', 'value')
)
def show_map(n_clicks, lat, lon, radius):
    if n_clicks == 0:
        return ""

    nearby = df_combined.copy()
    nearby["Distance"] = nearby.apply(lambda row: get_distance(lat, lon, row["Latitude"], row["Longitude"]), axis=1)
    nearby = nearby[nearby["Distance"] <= radius]

    fig = px.scatter_mapbox(
        nearby,
        lat="Latitude",
        lon="Longitude",
        hover_name="Site_ID",
        hover_data=["Licence_No", "Frequency", "Bandwidth_kHz"],
        color="Device_Type",
        zoom=8,
        height=900
    )

    # Always show POI marker
    fig.add_scattermapbox(
        lat=[lat],
        lon=[lon],
        mode='markers',
        marker=dict(size=18, color='limegreen', symbol="star"),
        name="Point of Interest"
    )

    fig.update_layout(mapbox_style="carto-positron")
    fig.update_layout(margin={"r":0,"t":0,"l":0,"b":0})

    fig.write_html("full_map.html")
    webbrowser.open_new_tab("full_map.html")
    return ""

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8050))
    app.run(host="0.0.0.0", port=port, debug=False)
