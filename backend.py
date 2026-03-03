# backend.py
import os
from dotenv import load_dotenv
load_dotenv()  # Load variables from .env file

import msal
import requests
from flask import Flask, jsonify

app = Flask(__name__)

# ==============================
# CONFIGURATION (Set as ENV variables for security)
# ==============================
TENANT_ID = os.getenv("AZURE_TENANT_ID", "your-tenant-id")
CLIENT_ID = os.getenv("AZURE_CLIENT_ID", "your-client-id")
CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET", "your-client-secret")
WORKSPACE_ID = os.getenv("PBI_WORKSPACE_ID", "your-workspace-id")
REPORT_ID = os.getenv("PBI_REPORT_ID", "your-report-id")


AUTHORITY_URL = f"https://login.microsoftonline.com/{TENANT_ID}"
SCOPE = ["https://analysis.windows.net/powerbi/api/.default"]
POWER_BI_API = "https://api.powerbi.com/v1.0/myorg"

# ==============================
# AUTHENTICATION
# ==============================
def get_access_token():
    """Authenticate with Azure AD and get an access token for Power BI API."""
    app_msal = msal.ConfidentialClientApplication(
        CLIENT_ID,
        authority=AUTHORITY_URL,
        client_credential=CLIENT_SECRET
    )
    result = app_msal.acquire_token_for_client(scopes=SCOPE)
    if "access_token" in result:
        return result["access_token"]
    else:
        raise Exception(f"Authentication failed: {result.get('error_description')}")

# ==============================
# GENERATE EMBED TOKEN
# ==============================
def generate_embed_token(access_token):
    """Generate an embed token for a specific report."""
    url = f"{POWER_BI_API}/groups/{WORKSPACE_ID}/reports/{REPORT_ID}/GenerateToken"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}"
    }
    body = {
        "accessLevel": "view"
    }
    response = requests.post(url, headers=headers, json=body)
    response.raise_for_status()
    return response.json()

# ==============================
# API ENDPOINT
# ==============================
@app.route("/getEmbedInfo", methods=["GET"])
def get_embed_info():
    try:
        access_token = get_access_token()
        embed_token_data = generate_embed_token(access_token)

        # Get report details
        report_url = f"{POWER_BI_API}/groups/{WORKSPACE_ID}/reports/{REPORT_ID}"
        headers = {"Authorization": f"Bearer {access_token}"}
        report_info = requests.get(report_url, headers=headers).json()

        return jsonify({
            "embedToken": embed_token_data["token"],
            "embedUrl": report_info["embedUrl"],
            "reportId": REPORT_ID
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

from flask import send_from_directory

@app.route("/")
def home():
    return send_from_directory(".", "index.html")

if __name__ == "__main__":
    app.run(debug=True)