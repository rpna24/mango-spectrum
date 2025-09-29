import os  
import re
import math
import webbrowser
import pandas as pd
from dash import Dash, dcc, html, Input, Output, State, callback_context, no_update
import plotly.graph_objects as go

# ------------------ Load Data (single Frequency_MHz) ------------------
df = pd.read_excel("Mango_Data.xlsx")

# Latitude/Longitude auto-detect (supports your GDA94 columns)
LAT_CANDIDATES  = ["Latitude", "Latitude_GDA94", "LPON_Centre_Lat", "Lat"]
LON_CANDIDATES  = ["Longitude", "Longitude_GDA94", "LPON_Centre_Long", "Nom_Longitude", "Long"]

lat_col = next((c for c in LAT_CANDIDATES if c in df.columns), None)
lon_col = next((c for c in LON_CANDIDATES if c in df.columns), None)
if not lat_col or not lon_col:
    raise ValueError("Latitude/Longitude columns not found. "
                     "Expected one of: "
                     f"{LAT_CANDIDATES} and {LON_CANDIDATES}")

# Frequency/Licensee detection
FREQ_COL = "Frequency_MHz"
if FREQ_COL not in df.columns:
    raise ValueError("Frequency_MHz column not found in the Excel file.")

LICENSEE_CANDIDATES = [
    "Licensee", "Licencee", "Licensee_Name", "Licencee_Name",
    "Licensee Name", "Licencee Name", "Client", "Client_Name",
    "Client Name", "Company", "Holder", "Holder_Name",
    "Org", "Organisation", "Organization"
]
LICENSEE_COL = next((c for c in LICENSEE_CANDIDATES if c in df.columns), None)

# Detect Licence No. and Site ID columns
LICENCE_NO_CANDS = [
    "Licence_No", "License_No", "Licence", "License", "LicenceNo", "LicenseNo",
    "Licence_Number", "License_Number", "Licence Number", "License Number", "LicenceID", "LicenseID"
]
SITE_ID_CANDS = ["Site_ID", "SiteID", "Site Id", "Site_No", "Site_Number", "Site Number", "Site"]

LICENCE_NO_COL = next((c for c in LICENCE_NO_CANDS if c in df.columns),None)
SITE_ID_COL    = next((c for c in SITE_ID_CANDS   if c in df.columns), None)

# Bandwidth handling (prefer Bandwidth_MHz, else convert from kHz, else 0)
if "Bandwidth_MHz" in df.columns:
    df["Bandwidth_MHz"] = pd.to_numeric(df["Bandwidth_MHz"], errors="coerce").fillna(0.0)
elif "Bandwidth_kHz" in df.columns:
    df["Bandwidth_MHz"] = pd.to_numeric(df["Bandwidth_kHz"], errors="coerce").fillna(0.0) / 1000.0
else:
    df["Bandwidth_MHz"] = 0.0

# Normalise Lat/Lon for internal use
df["Latitude"]  = pd.to_numeric(df[lat_col], errors="coerce").round(6)
df["Longitude"] = pd.to_numeric(df[lon_col], errors="coerce").round(6)

# Device_Type (keep if present, else single "ALL")
if "Device_Type" not in df.columns:
    df["Device_Type"] = "ALL"

# Keep only rows with valid frequency
df = df[pd.to_numeric(df[FREQ_COL], errors="coerce").notnull()].copy()
df["Frequency"] = pd.to_numeric(df[FREQ_COL], errors="coerce")
df["MinFrequency"] = df["Frequency"] - (df["Bandwidth_MHz"] / 2)
df["MaxFrequency"] = df["Frequency"] + (df["Bandwidth_MHz"] / 2)

# Use sensible slider bounds
FREQ_MIN = max(0, int(df["MinFrequency"].min() // 10 * 10))
FREQ_MAX = int((df["MaxFrequency"].max() // 10 + 1) * 10)

# Bandwidth slider bounds
BW_MIN = max(0, int(df["Bandwidth_MHz"].min() // 1 * 1))
BW_MAX = int((df["Bandwidth_MHz"].max() // 1 + 1) * 1)

# ------------------ Utils ------------------
def get_distance(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def assign_lanes(df_in: pd.DataFrame) -> pd.DataFrame:
    """Greedy interval packing per Device_Type for tidy stacks."""
    out = []
    ordered = df_in.sort_values(["Device_Type", "MinFrequency", "MaxFrequency"])
    for dev, g in ordered.groupby("Device_Type"):
        lanes = []  # latest MaxFrequency per lane
        for idx, row in g.iterrows():
            placed = False
            for lane_id, last_max in enumerate(lanes):
                if row["MinFrequency"] >= last_max:
                    lanes[lane_id] = row["MaxFrequency"]
                    out.append((idx, lane_id))
                    placed = True
                    break
            if not placed:
                lanes.append(row["MaxFrequency"])
                out.append((idx, len(lanes) - 1))
    lane_series = pd.Series({i: lane for i, lane in out})
    df_out = df_in.copy()
    df_out["Lane"] = df_out.index.map(lane_series)
    return df_out

def licensee_value(row) -> str:
    if LICENSEE_COL and LICENSEE_COL in row.index:
        val = row[LICENSEE_COL]
        if pd.notna(val):
            return str(val)
    return "N/A"

# --- Robust ID filter helpers (exact-normalized first, then contains) ---
def _norm_token(x) -> str:
    s = str(x)
    s = s.strip().upper()
    s = re.sub(r"[\s\-_]+", "", s)
    return s

def _split_tokens(query: str):
    if not query:
        return []
    parts = re.split(r"[;,]+", str(query))
    tokens = [_norm_token(p) for p in parts if str(p).strip() != ""]
    return tokens

def apply_id_filter(frame: pd.DataFrame, col: str | None, query: str | None) -> pd.DataFrame:
    """
    Robust filter for Licence No. or Site ID.
    - Accepts comma/semicolon separated values.
    - Exact normalized match OR contains normalized (fallback).
    """
    if not col or not query:
        return frame
    tokens = _split_tokens(query)
    if not tokens:
        return frame

    series_norm = frame[col].astype(str).map(_norm_token)
    mask = False
    for t in tokens:
        mask = mask | (series_norm == t)
    matched = frame[mask]
    if not matched.empty:
        return matched

    mask = False
    for t in tokens:
        mask = mask | series_norm.str.contains(t, na=False)
    return frame[mask]

def derive_center_radius(filtered: pd.DataFrame):
    """Mean lat/lon and radius that covers all filtered points (+5km, min 5km)."""
    if filtered.empty or filtered["Latitude"].isna().all() or filtered["Longitude"].isna().all():
        return None, None, None
    lat_c = float(filtered["Latitude"].mean())
    lon_c = float(filtered["Longitude"].mean())
    if len(filtered) == 1:
        return lat_c, lon_c, 5.0
    maxd = filtered.apply(lambda r: get_distance(lat_c, lon_c, r["Latitude"], r["Longitude"]), axis=1).max()
    return lat_c, lon_c, float(max(5.0, maxd + 5.0))

def derive_freq_span(filtered: pd.DataFrame):
    """Return [min, max] MHz covering filtered rows (with small padding)."""
    if filtered.empty:
        return None, None
    lo = float(filtered["MinFrequency"].min())
    hi = float(filtered["MaxFrequency"].max())
    lo = max(FREQ_MIN, math.floor(lo - 1))
    hi = min(FREQ_MAX, math.ceil(hi + 1))
    if hi < lo:
        lo, hi = hi, lo
    return int(lo), int(hi)

def derive_bw_span(filtered: pd.DataFrame):
    """Return [min, max] MHz covering filtered rows for Bandwidth."""
    if filtered.empty:
        return None, None
    lo = float(filtered["Bandwidth_MHz"].min())
    hi = float(filtered["Bandwidth_MHz"].max())
    lo = max(BW_MIN, math.floor(lo))
    hi = min(BW_MAX, math.ceil(hi))
    if hi < lo:
        lo, hi = hi, lo
    return int(lo), int(hi)

# ------------------ Dash App ------------------
app = Dash(__name__)
server = app.server
app.title = "Frequency Spectrum Viewer"

theme = {
    "background": "#0f172a",         # slate-900
    "card": "#111827",               # gray-900
    "accent": "#14b8a6",             # teal-500
    "accent2": "#60a5fa",            # blue-400
    "plot_bgcolor": "#0b1020",
    "font_color": "#e5e7eb"          # gray-200
}

input_style = {
    'padding': '10px', 'margin': '5px', 'width': '200px',
    'borderRadius': '8px', 'border': '1px solid #374151',
    'backgroundColor': '#1f2937', 'color': theme["font_color"]
}

button_style = {
    'padding': '12px 18px', 'margin': '6px 4px',
    'backgroundColor': theme["accent"], 'border': 'none',
    'color': '#0b1020', 'borderRadius': '10px', 'cursor': 'pointer',
    'fontWeight': '700', 'boxShadow': '0 6px 14px rgba(20,184,166,0.25)'
}

app.layout = html.Div([
    html.H1("📡Frequency Spectrum Viewer",

            style={'textAlign': 'center', 'color': theme["font_color"], 'marginBottom': '10px'}),
    # Controls
    html.Div([
        html.Div([
            html.Label("Latitude (− South):", style={'color': theme["font_color"]}),
            dcc.Input(id='latitude', type='number',
                      value= float(df["Latitude"].dropna().iloc[0]) if df["Latitude"].notna().any() else -28.300000,
                      step=0.000001, debounce= True, style=input_style),
        ]),
        html.Div([
            html.Label("Longitude (+ East):", style={'color': theme["font_color"]}),
            dcc.Input(id='longitude', type='number',
                      value=float(df["Longitude"].dropna().iloc[0]) if df["Longitude"].notna().any() else 153.500000,
                      step=0.000001, debounce=True, style=input_style),
        ]),
        html.Div([
            html.Label("Radius (km):", style={'color': theme["font_color"]}),
            dcc.Input(id='radius', type='number', value=25, debounce=True, style=input_style),
        ]),

        # Frequency Inputs (kept in sync with slider)
        html.Div([
            html.Label("Min Freq (MHz):", style={'color': theme["font_color"]}),
            dcc.Input(id='min_freq_input', type='number', value=7500, debounce=True, style=input_style),
        ]),
        html.Div([
            html.Label("Max Freq (MHz):", style={'color': theme["font_color"]}),
            dcc.Input(id='max_freq_input', type='number', value=8000, debounce=True, style=input_style),
        ]),

        html.Div([
            html.Label("Frequency Range (MHz):", style={'color': theme["font_color"], 'display': 'block'}),
            dcc.RangeSlider(
                id='freq_range', min=FREQ_MIN, max=FREQ_MAX, step=1,
                value=[7500, 8000], allowCross=False,
                tooltip={"always_visible": False}, marks=None, updatemode='mouseup'
            )
        ], style={'minWidth': '420px', 'maxWidth': '680px', 'flex': 1, 'padding': '0 10px'}),

        # -------- Bandwidth filter section --------
        html.Div([
            html.Label("Bandwidth (MHz) [Exact]:", style={'color': theme["font_color"]}),
            dcc.Input(id='bandwidth_input', type='number', value=None, debounce=True, placeholder="Enter bandwidth", style=input_style),
        ]),
        html.Div([
            html.Label("Bandwidth Range (MHz):", style={'color': theme["font_color"], 'display': 'block'}),
            dcc.RangeSlider(
                id='bw_range', min=BW_MIN, max=BW_MAX, step=1,
                value=[BW_MIN, BW_MAX], allowCross=False,
                tooltip={"always_visible": False}, marks=None, updatemode='mouseup'
            )
        ], style={'minWidth': '420px', 'maxWidth': '680px', 'flex': 1, 'padding': '0 10px'}),

        # -------- Filters (ID mode) --------
        html.Div([
            html.Label("Filter by Licence No. (comma/semicolon OK):", style={'color': theme["font_color"]}),
            dcc.Input(id='licence_filter', type='text', value='', debounce=True,
                      placeholder='e.g. 12345; ABC-987', style=input_style),
        ]),
        html.Div([
            html.Label("Filter by Site ID (comma/semicolon OK):", style={'color': theme["font_color"]}),
            dcc.Input(id='site_filter', type='text', value='', debounce=True,
                      placeholder='e.g. SITE-001, 77A', style=input_style),
        ]),

        html.Div([
            html.Button('🔎 Apply / Enter', id='submit-button', n_clicks=0, style=button_style),
            html.Button('🗺️ Nearby Map (new tab)', id='map-button', n_clicks=0, style=button_style),
        ], style={'alignSelf': 'end'})
    ], style={
        'display': 'flex', 'flexWrap': 'wrap', 'gap': '8px',
        'justifyContent': 'center', 'background': theme["card"],
        'borderRadius': '14px', 'padding': '14px', 'margin': '0 auto 18px auto',
        'maxWidth': '1200px', 'boxShadow': '0 10px 24px rgba(0,0,0,0.35)'
    }),

    # Spectrum
    dcc.Loading(children=dcc.Graph(id='spectrum-plot', style={'height': '640px'}), type='default'),

    # Click details
    html.Div(id='click-output', style={
        'marginTop': '16px', 'padding': '16px', 'borderRadius': '12px',
        'backgroundColor': theme["card"], 'color': theme["font_color"],
        'fontSize': '16px', 'display': 'none'
    }),

    # Dummy store for map callback
    dcc.Store(id="map-noop")
], style={'background': theme["background"], 'minHeight': '100vh', 'padding': '22px'})

# ------------------ Frequency inputs <-> slider sync (EXTENDED MINIMALLY) ------------------
@app.callback(
    Output('freq_range', 'value'),
    Output('min_freq_input', 'value'),
    Output('max_freq_input', 'value'),
    Output('bw_range', 'value'),
    Input('freq_range', 'value'),
    Input('min_freq_input', 'value'),
    Input('max_freq_input', 'value'),
    Input('licence_filter', 'value'),
    Input('site_filter', 'value'),
    Input('bw_range', 'value'),
    prevent_initial_call=False
)
def sync_freq_controls(slider_range, min_in, max_in, licence_query, site_query, bw_range):
    # If ID filter present and columns exist -> set freq/bw controls from matched rows
    applied = False
    filtered = df
    if (licence_query or "").strip() and LICENCE_NO_COL:
        filtered = apply_id_filter(filtered, LICENCE_NO_COL, licence_query); applied = True
    if (site_query or "").strip() and SITE_ID_COL:
        filtered = apply_id_filter(filtered, SITE_ID_COL, site_query); applied = True
    if applied and not filtered.empty:
        lo, hi = derive_freq_span(filtered)
        bw_lo, bw_hi = derive_bw_span(filtered)
        return [lo, hi], lo, hi, [bw_lo, bw_hi]

    # Otherwise, keep your original sync behavior
    trigger = (callback_context.triggered[0]['prop_id'].split('.')[0]
               if callback_context.triggered else None)

    cur_min, cur_max = slider_range if slider_range else (min_in or 7500, max_in or 8000)
    cur_bw_min, cur_bw_max = bw_range if bw_range else (BW_MIN, BW_MAX)

    def clamp_pair(lo, hi, minv, maxv):
        lo = max(minv, lo if lo is not None else minv)
        hi = min(maxv, hi if hi is not None else maxv)
        if hi < lo:
            lo, hi = hi, lo
        return int(lo), int(hi)

    if trigger == 'freq_range':
        lo, hi = clamp_pair(cur_min, cur_max, FREQ_MIN, FREQ_MAX)
        return [lo, hi], lo, hi, [cur_bw_min, cur_bw_max]
    if trigger == 'bw_range':
        bw_lo, bw_hi = clamp_pair(cur_bw_min, cur_bw_max, BW_MIN, BW_MAX)
        return [cur_min, cur_max], cur_min, cur_max, [bw_lo, bw_hi]

    lo, hi = clamp_pair(min_in if min_in is not None else cur_min,
                        max_in if max_in is not None else cur_max,
                        FREQ_MIN, FREQ_MAX)
    bw_lo, bw_hi = clamp_pair(cur_bw_min, cur_bw_max, BW_MIN, BW_MAX)
    return [lo, hi], lo, hi, [bw_lo, bw_hi]

# -------- when IDs are entered, auto-update Latitude & Longitude and set Radius=5 ----------
@app.callback(
    Output('latitude', 'value'),
    Output('longitude', 'value'),
    Output('radius', 'value'),
    Input('licence_filter', 'value'),
    Input('site_filter', 'value'),
    prevent_initial_call=True
)
def sync_lat_lon_radius_from_ids(licence_query, site_query):
    applied = False
    filtered = df
    if (licence_query or "").strip() and LICENCE_NO_COL:
        filtered = apply_id_filter(filtered, LICENCE_NO_COL, licence_query); applied = True
    if (site_query or "").strip() and SITE_ID_COL:
        filtered = apply_id_filter(filtered, SITE_ID_COL, site_query); applied = True

    if not applied or filtered.empty:
        return no_update, no_update, no_update

    lat_c, lon_c, _ = derive_center_radius(filtered)
    return lat_c, lon_c, 5  # set radius to 5 km

# ------------------ Spectrum plot ------------------
@app.callback(
    Output('spectrum-plot', 'figure'),
    Input('submit-button', 'n_clicks'),
    Input('latitude', 'value'),
    Input('longitude', 'value'),
    Input('radius', 'value'),
    Input('freq_range', 'value'),
    Input('bw_range', 'value'),
    Input('bandwidth_input', 'value'),
    Input('licence_filter', 'value'),
    Input('site_filter', 'value'),
    prevent_initial_call=False
)
def update_plot(n_clicks, lat, lon, radius, freq_range, bw_range, bandwidth_input, licence_query, site_query):
    # If ID filter mode is active -> ignore lat/lon, radius, freq_range and use only matched rows
    id_mode = bool((licence_query or "").strip() or (site_query or "").strip())

    # Helper to extract robust licence/site strings per row
    def _lic_site_vals(row):
        if LICENCE_NO_COL:
            lic_val = row.get(LICENCE_NO_COL)
            licence_val = "N/A" if pd.isna(lic_val) else str(lic_val)
        else:
            licence_val = "N/A"
        if SITE_ID_COL:
            site_raw = row.get(SITE_ID_COL)
            site_val = "N/A" if pd.isna(site_raw) else str(site_raw)
        else:
            site_val = "N/A"
        return licence_val, site_val

    if id_mode:
        filtered = df.copy()
        if LICENCE_NO_COL and (licence_query or "").strip():
            filtered = apply_id_filter(filtered, LICENCE_NO_COL, licence_query)
        if SITE_ID_COL and (site_query or "").strip():
            filtered = apply_id_filter(filtered, SITE_ID_COL,    site_query)

        if filtered.empty:
            fig = go.Figure()
            fig.update_layout(
                template='plotly_dark',
                paper_bgcolor=theme["background"],
                plot_bgcolor=theme["plot_bgcolor"],
                font_color=theme["font_color"],
                title="No allocation for the entered Licence/Site."
            )
            return fig

        # Derive frequency and bandwidth span strictly from filtered rows
        lo, hi = derive_freq_span(filtered)
        bw_lo, bw_hi = derive_bw_span(filtered)
        dynamic_min = lo if lo is not None else FREQ_MIN
        dynamic_max = hi if hi is not None else FREQ_MAX
        dynamic_bw_min = bw_lo if bw_lo is not None else BW_MIN
        dynamic_bw_max = bw_hi if bw_hi is not None else BW_MAX

<<<<<<< HEAD
        # Apply bandwidth filter
        filtered = filtered[(filtered["Bandwidth_MHz"] >= dynamic_bw_min) & (filtered["Bandwidth_MHz"] <= dynamic_bw_max)]

        # Apply bandwidth input filter if given
        if bandwidth_input is not None:
            filtered = filtered[filtered["Bandwidth_MHz"] == float(bandwidth_input)]

=======
>>>>>>> dc26b6df0fb9132c7cd471f12ae198f42ecd5747
        # Build bars (no distance filter; use all matched rows)
        filtered = filtered.sort_values(["Device_Type", "MinFrequency", "MaxFrequency"]).reset_index(drop=True)
        filtered = assign_lanes(filtered)

        traces = []
        color_map = {"TX": theme["accent2"], "RX": theme["accent"], "ALL": "#a78bfa"}
        y_labels = []

        for dev in sorted(filtered["Device_Type"].unique(), key=lambda x: {"TX":0,"RX":1}.get(x, 2)):
            g = filtered[filtered["Device_Type"] == dev]
            for _, r in g.iterrows():
                lane_name = f"{dev} L{int(r['Lane'])}"
                y_labels.append(lane_name)
                licence_val, site_val = _lic_site_vals(r)
                lic_str = licensee_value(r)
                traces.append(go.Bar(
                    y=[lane_name],
                    x=[r["MaxFrequency"] - r["MinFrequency"]],
                    base=r["MinFrequency"],
                    orientation='h',
                    marker=dict(color=color_map.get(dev, "#94a3b8"), opacity=0.9),
                    hoverinfo='text',
                    customdata=[[r.Frequency, r.Bandwidth_MHz, r.Latitude, r.Longitude,
                                 licence_val, site_val, r.Device_Type, lic_str]],
                    hovertemplate="<b>%{customdata[6]}</b><br>"
                                  "Freq: %{customdata[0]:.3f} MHz<br>"
                                  "BW: %{customdata[1]:.3f} MHz<br>"
                                  "Licensee: %{customdata[7]}<br>"
                                  "Lat: %{customdata[2]} | Lon: %{customdata[3]}<br>"
                                  "Licence: %{customdata[4]} | Site: %{customdata[5]}<extra></extra>",
                    showlegend=False
                ))

        y_labels = sorted(set(y_labels), key=lambda s: (s.split()[0], int(s.split('L')[-1])))

        fig = go.Figure(data=traces)
        fig.update_layout(
            title="Filtered Frequency Spectrum (ID filter mode: lat/lon & ranges ignored)",
            barmode='overlay',
            xaxis=dict(title="Frequency (MHz)", range=[dynamic_min, dynamic_max]),
            yaxis=dict(categoryorder='array', categoryarray=y_labels, title=None),
            height=640,
            template='plotly_dark',
            paper_bgcolor=theme["background"],
            plot_bgcolor=theme["plot_bgcolor"],
            font_color=theme["font_color"],
            margin=dict(l=60, r=20, t=60, b=40)
        )
        return fig

    # ---------- Normal mode (no ID filters) ----------
    if None in [lat, lon, radius] or freq_range is None or bw_range is None:
        return go.Figure()

    min_freq, max_freq = freq_range
    min_bw, max_bw = bw_range
    filtered = df[
        (df["MaxFrequency"] >= min_freq) &
        (df["MinFrequency"] <= max_freq) &
        (df["Bandwidth_MHz"] >= min_bw) &
        (df["Bandwidth_MHz"] <= max_bw)
    ].copy()

    # Apply bandwidth input filter if given
    if bandwidth_input is not None:
        filtered = filtered[filtered["Bandwidth_MHz"] == float(bandwidth_input)]

    # Distance filter
    filtered["Distance"] = filtered.apply(
        lambda r: get_distance(lat, lon, r["Latitude"], r["Longitude"]), axis=1)
    filtered = filtered[filtered["Distance"] <= radius]

    if filtered.empty:
        fig = go.Figure()
        fig.update_layout(
            template='plotly_dark',
            paper_bgcolor=theme["background"],
            plot_bgcolor=theme["plot_bgcolor"],
            font_color=theme["font_color"],
            title="No allocations in the selected range and radius."
        )
        return fig

    filtered = filtered.sort_values(["Device_Type", "MinFrequency", "MaxFrequency"]).reset_index(drop=True)
    filtered = assign_lanes(filtered)

    traces = []
    color_map = {"TX": theme["accent2"], "RX": theme["accent"], "ALL": "#a78bfa"}  # purple for ALL
    y_labels = []

    # robust licence/site values in NORMAL MODE as well
    def _lic_site_vals(row):
        if LICENCE_NO_COL:
            lic_val = row.get(LICENCE_NO_COL)
            licence_val = "N/A" if pd.isna(lic_val) else str(lic_val)
        else:
            licence_val = "N/A"
        if SITE_ID_COL:
            site_raw = row.get(SITE_ID_COL)
            site_val = "N/A" if pd.isna(site_raw) else str(site_raw)
        else:
            site_val = "N/A"
        return licence_val, site_val

    for dev in sorted(filtered["Device_Type"].unique(), key=lambda x: {"TX":0,"RX":1}.get(x, 2)):
        g = filtered[filtered["Device_Type"] == dev]
        for _, r in g.iterrows():
            lane_name = f"{dev} L{int(r['Lane'])}"
            y_labels.append(lane_name)
            licence_val, site_val = _lic_site_vals(r)
            lic_str = licensee_value(r)
            traces.append(go.Bar(
                y=[lane_name],
                x=[r["MaxFrequency"] - r["MinFrequency"]],
                base=r["MinFrequency"],
                orientation='h',
                marker=dict(color=color_map.get(dev, "#94a3b8"), opacity=0.9),
                hoverinfo='text',
                customdata=[[r.Frequency, r.Bandwidth_MHz, r.Latitude, r.Longitude,
                             licence_val, site_val, r.Device_Type, lic_str]],
                hovertemplate="<b>%{customdata[6]}</b><br>"
                              "Freq: %{customdata[0]:.3f} MHz<br>"
                              "BW: %{customdata[1]:.3f} MHz<br>"
                              "Licensee: %{customdata[7]}<br>"
                              "Lat: %{customdata[2]} | Lon: %{customdata[3]}<br>"
                              "Licence: %{customdata[4]} | Site: %{customdata[5]}<extra></extra>",
                showlegend=False
            ))

    y_labels = sorted(set(y_labels), key=lambda s: (s.split()[0], int(s.split('L')[-1])))
    dynamic_min = max(min_freq - 5, 0)
    dynamic_max = max_freq + 5

    fig = go.Figure(data=traces)
    fig.update_layout(
        title="Filtered Frequency Spectrum (normal mode)",
        barmode='overlay',
        xaxis=dict(title="Frequency (MHz)", range=[dynamic_min, dynamic_max]),
        yaxis=dict(categoryorder='array', categoryarray=y_labels, title=None),
        height=640,
        template='plotly_dark',
        paper_bgcolor=theme["background"],
        plot_bgcolor=theme["plot_bgcolor"],
        font_color=theme["font_color"],
        margin=dict(l=60, r=20, t=60, b=40)
    )
    return fig

# ------------------ Click details (robust to missing fields) ------------------
@app.callback(
    Output('click-output', 'children'),
    Output('click-output', 'style'),
    Input('spectrum-plot', 'clickData')
)
def display_click_info(clickData):
    if not clickData or 'points' not in clickData or not clickData['points']:
        return "", {'display': 'none'}

    p = clickData['points'][0].get('customdata') or []
    # Expected order: [freq, bw, lat, lon, licence_no, site_id, dev, licensee]
    def _get(i, default="N/A"):
        try:
            v = p[i]
            if v is None or (isinstance(v, float) and pd.isna(v)):
                return default
            return v
        except Exception:
            return default

    freq      = _get(0, None)
    bw        = _get(1, None)
    lat       = _get(2, "N/A")
    lon       = _get(3, "N/A")
    licence   = _get(4, "N/A")
    site_id   = _get(5, "N/A")
    dev       = _get(6, "N/A")
    licensee  = _get(7, "N/A")

    # Nicely formatted strings
    freq_s = f"{freq:.3f} MHz" if isinstance(freq, (int, float)) else str(freq)
    bw_s   = f"{bw:.3f} MHz"   if isinstance(bw,   (int, float)) else str(bw)

    box = html.Div([
        html.H3("📋 Details", style={'marginTop': 0}),
        html.P(f"Type: {dev}"),
        html.P(f"Licence No: {licence}"),
        html.P(f"Licensee: {licensee}"),
        html.P(f"Site ID: {site_id}"),
        html.P(f"Latitude: {lat}"),
        html.P(f"Longitude: {lon}"),
        html.P(f"Frequency: {freq_s}"),
        html.P(f"Bandwidth: {bw_s}"),
    ])
    style = {
        'display': 'block',
        'marginTop': '12px',
        'padding': '16px',
        'borderRadius': '12px',
        'backgroundColor': theme["card"],
        'color': theme["font_color"],
        'fontSize': '16px'
    }
    return box, style

# -------- Nearby Map in a NEW TAB --------
@app.callback(
    Output('map-noop', 'data'),
    Input('map-button', 'n_clicks'),
    State('latitude', 'value'),
    State('longitude', 'value'),
    State('radius', 'value'),
    State('freq_range', 'value'),
    State('bw_range', 'value'),
    State('bandwidth_input', 'value'),
    State('licence_filter', 'value'),
    State('site_filter', 'value'),
    prevent_initial_call=True
)
def open_map_new_tab(n_clicks, lat, lon, radius, freq_range, bw_range, bandwidth_input, licence_query, site_query):
    if not n_clicks:
        return {"opened": False}

    id_mode = bool((licence_query or "").strip() or (site_query or "").strip())
    fig = go.Figure()

    def dev_color(d):
        return {"TX": "#60a5fa", "RX": "#14b8a6"}.get(d, "#fa8b8b")

    if id_mode:
        # ID filter mode: ignore controls; plot only matched rows and center on them
        nearby = df.copy()
        if LICENCE_NO_COL and (licence_query or "").strip():
            nearby = apply_id_filter(nearby, LICENCE_NO_COL, licence_query)
        if SITE_ID_COL and (site_query or "").strip():
            nearby = apply_id_filter(nearby, SITE_ID_COL,    site_query)

        # Apply bandwidth filter
        bw_lo, bw_hi = derive_bw_span(nearby)
        bw_min = bw_lo if bw_lo is not None else BW_MIN
        bw_max = bw_hi if bw_hi is not None else BW_MAX
        nearby = nearby[(nearby["Bandwidth_MHz"] >= bw_min) & (nearby["Bandwidth_MHz"] <= bw_max)]
        if bandwidth_input is not None:
            nearby = nearby[nearby["Bandwidth_MHz"] == float(bandwidth_input)]

        if nearby.empty:
            center_lat = lat if lat is not None else float(df["Latitude"].dropna().iloc[0])
            center_lon = lon if lon is not None else float(df["Longitude"].dropna().iloc[0])
            map_center = dict(lat=center_lat, lon=center_lon)
        else:
            c_lat, c_lon, _ = derive_center_radius(nearby)
            map_center = dict(lat=c_lat, lon=c_lon)

            lic_series = (nearby[LICENSEE_COL].fillna("N/A").astype(str).values
                          if LICENSEE_COL and LICENSEE_COL in nearby.columns
                          else ["N/A"] * len(nearby))
            lic_no_series = (nearby[LICENCE_NO_COL].astype(str).values
                             if LICENCE_NO_COL and LICENCE_NO_COL in nearby.columns
                             else ["N/A"] * len(nearby))
            site_series = (nearby[SITE_ID_COL].astype(str).values
                           if SITE_ID_COL and SITE_ID_COL in nearby.columns
                           else ["N/A"] * len(nearby))

            fig.add_trace(go.Scattermapbox(
                lat=nearby['Latitude'],
                lon=nearby['Longitude'],
                mode='markers',
                marker=dict(size=10, color=[dev_color(t) for t in nearby['Device_Type']]),
                text=site_series,
                hovertemplate=(
                    "<b>%{text}</b><br>" +
                    "Device: %{customdata[0]}<br>" +
                    "Licence: %{customdata[1]}<br>" +
                    "Licensee: %{customdata[4]}<br>" +
                    "Freq: %{customdata[2]:.3f} MHz<br>" +
                    "BW: %{customdata[3]:.3f} MHz<extra></extra>"
                ),
                customdata=pd.DataFrame({
                    "Device": nearby['Device_Type'].astype(str),
                    "Licence": lic_no_series,
                    "FreqMHz": nearby['Frequency'].astype(float),
                    "BWMHz": nearby['Bandwidth_MHz'].astype(float),
                    "Licensee": lic_series
                }).values,
                name="Matched Sites"
            ))

            # POI (center of matched rows)
            fig.add_trace(go.Scattermapbox(
                lat=[map_center['lat']], lon=[map_center['lon']], mode='markers',
                marker=dict(size=34, color='black', symbol='circle'),
                hoverinfo='skip', showlegend=False
            ))
            fig.add_trace(go.Scattermapbox(
                lat=[map_center['lat']], lon=[map_center['lon']], mode='markers',
                marker=dict(size=28, color='#FFD400', symbol='star'),
                name="Point of Interest (ID center)",
                hovertext=["Point of Interest"],
                hoverinfo="text",
                showlegend=True
            ))
        fig.update_layout(
            mapbox_style='open-street-map',
            mapbox=dict(center=map_center, zoom=10),
            margin=dict(r=0, t=0, l=0, b=0),
            paper_bgcolor=theme["background"],
            font_color=theme["font_color"],
            legend=dict(title=None)
        )

    else:
        # Normal mode: respect controls & filters by range/radius
        min_freq, max_freq = freq_range
        min_bw, max_bw = bw_range
        nearby = df[
            (df["MaxFrequency"] >= min_freq) &
            (df["MinFrequency"] <= max_freq) &
            (df["Bandwidth_MHz"] >= min_bw) &
            (df["Bandwidth_MHz"] <= max_bw)
        ].copy()
        if bandwidth_input is not None:
            nearby = nearby[nearby["Bandwidth_MHz"] == float(bandwidth_input)]
        nearby["Distance"] = nearby.apply(
            lambda r: get_distance(lat, lon, r["Latitude"], r["Longitude"]), axis=1)
        nearby = nearby[nearby["Distance"] <= radius]

        if not nearby.empty:
            lic_series = (nearby[LICENSEE_COL].fillna("N/A").astype(str).values
                          if LICENSEE_COL and LICENSEE_COL in nearby.columns
                          else ["N/A"] * len(nearby))
            lic_no_series = (nearby[LICENCE_NO_COL].astype(str).values
                             if LICENCE_NO_COL and LICENCE_NO_COL in nearby.columns
                             else ["N/A"] * len(nearby))
            site_series = (nearby[SITE_ID_COL].astype(str).values
                           if SITE_ID_COL and SITE_ID_COL in nearby.columns
                           else ["N/A"] * len(nearby))

            fig.add_trace(go.Scattermapbox(
                lat=nearby['Latitude'],
                lon=nearby['Longitude'],
                mode='markers',
                marker=dict(size=10, color=[dev_color(t) for t in nearby['Device_Type']]),
                text=site_series,
                hovertemplate=(
                    "<b>%{text}</b><br>" +
                    "Device: %{customdata[0]}<br>" +
                    "Licence: %{customdata[1]}<br>" +
                    "Licensee: %{customdata[4]}<br>" +
                    "Freq: %{customdata[2]:.3f} MHz<br>" +
                    "BW: %{customdata[3]:.3f} MHz<extra></extra>"
                ),
                customdata=pd.DataFrame({
                    "Device": nearby['Device_Type'].astype(str),
                    "Licence": lic_no_series,
                    "FreqMHz": nearby['Frequency'].astype(float),
                    "BWMHz": nearby['Bandwidth_MHz'].astype(float),
                    "Licensee": lic_series
                }).values,
                name="Nearby Sites"
            ))

        # POI outline for contrast + yellow star
        fig.add_trace(go.Scattermapbox(
            lat=[lat], lon=[lon], mode='markers',
            marker=dict(size=34, color='black', symbol='circle'),
            hoverinfo='skip', showlegend=False
        ))
        fig.add_trace(go.Scattermapbox(
            lat=[lat], lon=[lon], mode='markers',
            marker=dict(size=28, color='#FFD400', symbol='star'),
            name="Point of Interest",
            hovertext=["Point of Interest"],
            hoverinfo="text",
            showlegend=True
        ))

        fig.update_layout(
            mapbox_style='open-street-map',
            mapbox=dict(center=dict(lat=lat, lon=lon), zoom=10),
            margin=dict(r=0, t=0, l=0, b=0),
            paper_bgcolor=theme["background"],
            font_color=theme["font_color"],
            legend=dict(title=None)
        )

    html_path = os.path.abspath("full_map.html")
    fig.write_html(html_path, include_plotlyjs=True, full_html=True)
    webbrowser.open_new_tab(f"file://{html_path}")

    return {"opened": True, "path": html_path}

# ------------------ Main ------------------
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8050))
    app.run(host="0.0.0.0", port=port, debug=False)