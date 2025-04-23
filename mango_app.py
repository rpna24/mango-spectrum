from dash import Dash, html
import os

app = Dash(__name__)
server = app.server  # 🔥 This is required by gunicorn

app.layout = html.Div([
    html.H1("✅ Hello from Mango Viewer"),
    html.P("This is a deployment test on Render.")
])

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8050))
    app.run(host="0.0.0.0", port=port)
