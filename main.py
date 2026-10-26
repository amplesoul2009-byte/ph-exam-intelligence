import os
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI(
    title="0xGREY",
    description="Premium Mock Test Platform"
)

@app.get("/", response_class=HTMLResponse)
async def home():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <meta name="viewport"
              content="width=device-width, initial-scale=1">
        <title>0xGREY</title>
        <style>
            body {
                background: #101827;
                color: white;
                font-family: Arial, sans-serif;
                text-align: center;
                padding: 60px 15px;
            }
            .card {
                max-width: 500px;
                margin: auto;
                padding: 30px;
                background: #1c293b;
                border-radius: 16px;
            }
            h1 { color: #60a5fa; }
        </style>
    </head>
    <body>
        <div class="card">
            <h1>0xGREY</h1>
            <h2>Premium Mock Test Platform</h2>
            <p>Your examination platform is being prepared.</p>
            <p>Practice. Analyse. Improve.</p>
        </div>
    </body>
    </html>
    """

@app.get("/health")
async def health():
    return {
        "status": "online",
        "platform": "0xGREY",
        "version": "1.0"
    }
