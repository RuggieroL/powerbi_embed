from fastapi import FastAPI
from pydantic import BaseModel
import msal
import requests
import os
from dotenv import load_dotenv

load_dotenv()

TENANT_ID = os.getenv("TENANT_ID")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")

WORKSPACE_ID = os.getenv("WORKSPACE_ID")
REPORT_ID = os.getenv("REPORT_ID")

AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
SCOPE = ["https://analysis.windows.net/powerbi/api/.default"]
POWERBI_API_ROOT = "https://api.powerbi.com/v1.0/myorg/"

app = FastAPI()

def get_access_token():
    app_client = msal.ConfidentialClientApplication(
        CLIENT_ID,
        authority=AUTHORITY,
        client_credential=CLIENT_SECRET
    )
    token = app_client.acquire_token_silent(SCOPE, account=None)
    if not token:
        token = app_client.acquire_token_for_client(scopes=SCOPE)
    return token["access_token"]

@app.get("/embed-info")
def get_embed_info():
    access_token = get_access_token()

    # Get embed URL
    report_url = f"{POWERBI_API_ROOT}groups/{WORKSPACE_ID}/reports/{REPORT_ID}"
    headers = {"Authorization": f"Bearer {access_token}"}
    report_res = requests.get(report_url, headers=headers).json()
    embed_url = report_res["embedUrl"]

    # Generate Embed Token
    token_url = f"{POWERBI_API_ROOT}groups/{WORKSPACE_ID}/reports/{REPORT_ID}/GenerateToken"
    embed_token_res = requests.post(
        token_url,
        headers=headers,
        json={"accessLevel": "view"}
    ).json()

    return {
        "reportId": REPORT_ID,
        "embedUrl": embed_url,
        "embedToken": embed_token_res["token"]
    }
