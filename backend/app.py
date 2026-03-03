# backend/app.py
import sys
print("Python executable:", sys.executable)
print("sys.path:", sys.path[:3])  # prime entries

import os
import json
import requests
from flask import Flask, request, jsonify, send_from_directory
from dotenv import load_dotenv
from msal import ConfidentialClientApplication

# ====== Carica .env dalla root del progetto ======
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(BASE_DIR, ".env"))

POWER_BI_API = "https://api.powerbi.com/v1.0/myorg"

# ====== Credenziali SP da .env ======
TENANT_ID     = os.getenv("AZURE_TENANT_ID")
CLIENT_ID     = os.getenv("AZURE_CLIENT_ID")
CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET")

# ====== Default PBI (override possibili via POST) ======
DEFAULT_WORKSPACE_ID = os.getenv("PBI_WORKSPACE_ID")
DEFAULT_REPORT_ID    = os.getenv("PBI_REPORT_ID")
DEFAULT_DASHBOARD_ID = os.getenv("PBI_DASHBOARD_ID")

# ====== RLS defaults / bypass ======
DEFAULT_RLS_USERNAME = os.getenv("PBI_RLS_USERNAME", "")
RLS_ROLES_RAW        = os.getenv("PBI_RLS_ROLES", "")
BYPASS_ROLE          = os.getenv("PBI_RLS_BYPASS_ROLE", "AllData")  # ruolo senza filtri

# ====== Timeout per le chiamate HTTP (connessione, lettura) ======
REQUEST_TIMEOUT = (5, 30)

def parse_roles(value: str):
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list) and all(isinstance(x, str) for x in parsed):
            return [x.strip() for x in parsed if x.strip()]
    except Exception:
        pass
    return [x.strip() for x in value.split(",") if x.strip()]

DEFAULT_RLS_ROLES = parse_roles(RLS_ROLES_RAW)
TOKEN_TTL_MIN = int(os.getenv("PBI_TOKEN_TTL", "60"))

AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
SCOPE     = ["https://analysis.windows.net/powerbi/api/.default"]

app = Flask(__name__, static_folder="frontend", static_url_path="")

def require_env(var_name: str):
    val = os.getenv(var_name)
    if not val:
        raise RuntimeError(f"Missing required environment variable: {var_name}")
    return val

# ====== Esegue autenticazione su AZURE ======
def acquire_app_token():
    require_env("AZURE_TENANT_ID")
    require_env("AZURE_CLIENT_ID")
    require_env("AZURE_CLIENT_SECRET")
    cca = ConfidentialClientApplication(CLIENT_ID, authority=AUTHORITY, client_credential=CLIENT_SECRET)
    token_result = cca.acquire_token_for_client(scopes=SCOPE)
    if "access_token" not in token_result:
        raise RuntimeError(f"Acquire token failed: {token_result}")
    return token_result["access_token"]

@app.route("/")
def index():
    return send_from_directory(os.path.join(BASE_DIR, "frontend"), "index.html")

@app.get("/api/defaults")
def get_defaults():
    return jsonify({
        "workspaceId": DEFAULT_WORKSPACE_ID or "",
        "reportId":    DEFAULT_REPORT_ID or "",
        "dashboardId": DEFAULT_DASHBOARD_ID or "",
        "username":    DEFAULT_RLS_USERNAME or "",
        "roles":       DEFAULT_RLS_ROLES,
        "ignoreRls":   False,
        "bypassRole":  BYPASS_ROLE or "AllData"
    })

@app.get("/api/workspaces")
def list_workspaces():
    """
    Ritorna l'elenco dei workspace (groups) accessibili:
    [{ id, name, isReadOnly, isOnDedicatedCapacity }]
    Docs: GET /groups (Get Groups)
    """
    try:
        bearer = acquire_app_token()
    except Exception as ex:
        return jsonify(error=f"Auth error: {ex}"), 500

    headers = {"Authorization": f"Bearer {bearer}"}
    # Get Groups -> lista workspaces
    r = requests.get(f"{POWER_BI_API}/groups", headers=headers, timeout=REQUEST_TIMEOUT)
    if not r.ok:
        return jsonify(error=f"Get groups failed [{r.status_code}]", details=r.text), r.status_code

    values = r.json().get("value", [])
    workspaces = [{
        "id": it.get("id"),
        "name": it.get("name"),
        "isReadOnly": it.get("isReadOnly"),
        "isOnDedicatedCapacity": it.get("isOnDedicatedCapacity")
    } for it in values]

    return jsonify({"workspaces": workspaces})

@app.get("/api/list-artifacts")
def list_artifacts():
    """
    ?workspaceId=<GUID>
    Ritorna: { reports: [{id,name,embedUrl,datasetId?}], dashboards: [{id,displayName,embedUrl}] }
    """
    workspace_id = request.args.get("workspaceId") or DEFAULT_WORKSPACE_ID
    if not workspace_id:
        return jsonify(error="workspaceId is required"), 400

    try:
        bearer = acquire_app_token()
    except Exception as ex:
        return jsonify(error=f"Auth error: {ex}"), 500

    headers = {"Authorization": f"Bearer {bearer}"}

    # Reports in group
    r = requests.get(f"{POWER_BI_API}/groups/{workspace_id}/reports", headers=headers, timeout=REQUEST_TIMEOUT)
    if not r.ok:
        return jsonify(error=f"Get reports failed [{r.status_code}]", details=r.text), r.status_code
    reps = r.json().get("value", [])

    # Dashboards in group
    d = requests.get(f"{POWER_BI_API}/groups/{workspace_id}/dashboards", headers=headers, timeout=REQUEST_TIMEOUT)
    if not d.ok:
        return jsonify(error=f"Get dashboards failed [{d.status_code}]", details=d.text), d.status_code
    dashes = d.json().get("value", [])

    reports = [{
        "id": it.get("id"),
        "name": it.get("name"),
        "embedUrl": it.get("embedUrl"),
        "datasetId": it.get("datasetId")
    } for it in reps]

    dashboards = [{
        "id": it.get("id"),
        "displayName": it.get("displayName"),
        "embedUrl": it.get("embedUrl")
    } for it in dashes]

    return jsonify({"reports": reports, "dashboards": dashboards})

@app.get("/api/report/pages")
def list_report_pages():
    """
    ?workspaceId=<GUID>&reportId=<GUID>
    Ritorna: { pages: [{ name, displayName, order }] }
    """
    group_id  = request.args.get("workspaceId") or DEFAULT_WORKSPACE_ID
    report_id = request.args.get("reportId")    or DEFAULT_REPORT_ID
    if not group_id or not report_id:
        return jsonify(error="workspaceId and reportId are required"), 400

    try:
        bearer = acquire_app_token()
    except Exception as ex:
        return jsonify(error=f"Auth error: {ex}"), 500

    headers = {"Authorization": f"Bearer {bearer}"}
    resp = requests.get(f"{POWER_BI_API}/groups/{group_id}/reports/{report_id}/pages",
                        headers=headers, timeout=REQUEST_TIMEOUT)
    if not resp.ok:
        return jsonify(error=f"Get pages failed [{resp.status_code}]", details=resp.text), resp.status_code

    values = resp.json().get("value", [])
    pages = [{"name": p.get("name"), "displayName": p.get("displayName"), "order": p.get("order")} for p in values]
    try:
        pages.sort(key=lambda x: int(x["order"]) if x["order"] is not None else 0)
    except Exception:
        pass
    return jsonify({"pages": pages})

# ====== Cerca i dataset in backend rispetto all'item selezionato: report o dashboard ======
def discover_report_dataset_id(headers, group_id, report_id):
    r = requests.get(f"{POWER_BI_API}/groups/{group_id}/reports/{report_id}", headers=headers, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    js = r.json()
    return js.get("embedUrl"), js.get("datasetId")

def discover_dashboard_dataset_ids(headers, group_id, dashboard_id):
    d = requests.get(f"{POWER_BI_API}/groups/{group_id}/dashboards/{dashboard_id}", headers=headers, timeout=REQUEST_TIMEOUT)
    d.raise_for_status()
    embed_url = d.json().get("embedUrl")
    t = requests.get(f"{POWER_BI_API}/groups/{group_id}/dashboards/{dashboard_id}/tiles", headers=headers, timeout=REQUEST_TIMEOUT)
    t.raise_for_status()
    values = (t.json() or {}).get("value", [])
    ds_ids = {tile.get("datasetId") for tile in values if tile.get("datasetId")}
    return embed_url, sorted(ds_ids)

# ====== Riconoscimento errori EI ======
def _requires_effective_identity(err_json: dict) -> bool:
    """Rileva errori che chiedono EI/ruoli."""
    s = json.dumps(err_json or {}).lower()
    needles = [
        "requires effective identity",
        "requires roles to be included",
        "requires role",
        "username to be identical to the caller's principal name"
    ]
    return any(n in s for n in needles)

def _shouldnt_have_effective_identity(err_json: dict) -> bool:
    """Rileva errori che dicono di NON inviare EI."""
    s = json.dumps(err_json or {}).lower()
    return "shouldn't have effective identity" in s or "should not have effective identity" in s

@app.post("/api/generate-token")
def generate_token_unified():
    """Genera un embed token per report o dashboard, con gestione intelligente di EffectiveIdentity."""
    body = request.get_json(silent=True) or {}
    artifact_type = (body.get("artifactType") or "report").lower()
    workspace_id  = body.get("workspaceId") or DEFAULT_WORKSPACE_ID
    report_id     = body.get("reportId")    or DEFAULT_REPORT_ID
    dashboard_id  = body.get("dashboardId") or DEFAULT_DASHBOARD_ID
    ignore_rls    = bool(body.get("ignoreRls", False))
    username      = body.get("username") or DEFAULT_RLS_USERNAME
    roles         = body.get("roles")
    page_name     = body.get("pageName")  # opzionale

    if isinstance(roles, str):
        roles = parse_roles(roles)
    if roles is None:
        roles = DEFAULT_RLS_ROLES

    missing = []
    if not workspace_id: missing.append("workspaceId")
    if artifact_type not in ("report", "dashboard"):
        return jsonify(error="artifactType deve essere 'report' o 'dashboard'"), 400
    if artifact_type == "report" and not report_id: missing.append("reportId")
    if artifact_type == "dashboard" and not dashboard_id: missing.append("dashboardId")
    if missing:
        return jsonify(error="Missing required fields", details=", ".join(missing)), 400

    try:
        bearer = acquire_app_token()
    except Exception as ex:
        return jsonify(error=f"Auth error: {ex}"), 500
    headers = {"Authorization": f"Bearer {bearer}", "Content-Type": "application/json"}

    # ====== REPORT ======
    if artifact_type == "report":
        try:
            embed_url, dataset_id = discover_report_dataset_id(headers, workspace_id, report_id)
        except requests.HTTPError as ex:
            resp = ex.response
            return jsonify(error=f"GET report failed [{resp.status_code}]", details=resp.text), resp.status_code
        if not dataset_id:
            return jsonify(error="Impossibile determinare il datasetId dal report."), 400

        gen_url = f"{POWER_BI_API}/GenerateToken"

        # 1) Primo tentativo: NESSUNA 'identities' (copre dataset senza RLS o che non richiedono EI)
        payload_base = {
            "reports":  [{"id": report_id, "groupId": workspace_id}],
            "datasets": [{"id": dataset_id}],
            "lifetimeInMinutes": TOKEN_TTL_MIN
        }
        res1 = requests.post(gen_url, headers=headers, json=payload_base, timeout=REQUEST_TIMEOUT)
        if res1.ok:
            token = res1.json()["token"]
            return jsonify(
                artifactType="report",
                embedUrl=embed_url,
                reportId=report_id,
                accessToken=token,
                tokenType="Embed",
                pageName=page_name or ""
            )

        # Esamina errore
        try:
            err1_json = res1.json()
        except Exception:
            err1_json = {"raw": res1.text}

        # Se l'errore dice che NON dovremmo inviare EI, restituisci l'errore originale
        if _shouldnt_have_effective_identity(err1_json):
            return jsonify(error=f"GenerateToken failed [{res1.status_code}]", details=err1_json), res1.status_code

        # 2) Fallback: l'API richiede EI/ruoli → costruisci 'identities'
        if _requires_effective_identity(err1_json):
            identities = []
            if ignore_rls:
                bypass_username = username or os.getenv("PBI_RLS_USERNAME") or "embed-bypass@local"
                identity_obj = {
                    "username": bypass_username,
                    "datasets": [dataset_id]
                }
                # Inserisci ruolo di bypass solo se definito
                if BYPASS_ROLE:
                    identity_obj["roles"] = [BYPASS_ROLE]
                identities.append(identity_obj)
            else:
                if not username or not roles:
                    return jsonify(
                        error="EffectiveIdentity richiesta dal dataset, ma username/roles non forniti.",
                        details="Imposta 'username' e 'roles' oppure abilita 'ignoreRls' con un ruolo di bypass."
                    ), 400
                identities.append({
                    "username": username,
                    "roles": roles,
                    "datasets": [dataset_id]
                })

            payload_fallback = dict(payload_base)
            payload_fallback["identities"] = identities
            res2 = requests.post(gen_url, headers=headers, json=payload_fallback, timeout=REQUEST_TIMEOUT)
            if res2.ok:
                token = res2.json()["token"]
                return jsonify(
                    artifactType="report",
                    embedUrl=embed_url,
                    reportId=report_id,
                    accessToken=token,
                    tokenType="Embed",
                    pageName=page_name or ""
                )

            try:
                err2_json = res2.json()
            except Exception:
                err2_json = {"raw": res2.text}
            return jsonify(error=f"GenerateToken failed [{res2.status_code}]", details=err2_json), res2.status_code

        # Errore diverso (non correlato a EI) → ritorna l'errore
        return jsonify(error=f"GenerateToken failed [{res1.status_code}]", details=err1_json), res1.status_code

    # ====== DASHBOARD ======
    try:
        embed_url, ds_list = discover_dashboard_dataset_ids(headers, workspace_id, dashboard_id)
    except requests.HTTPError as ex:
        resp = ex.response
        return jsonify(error=f"GET dashboard/tiles failed [{resp.status_code}]", details=resp.text), resp.status_code

    gen_url = f"{POWER_BI_API}/groups/{workspace_id}/dashboards/{dashboard_id}/GenerateToken"

    def post_dash_token(payload):
        return requests.post(gen_url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)

    payload = {"accessLevel": "View", "lifetimeInMinutes": TOKEN_TTL_MIN}

    if ignore_rls:
        res = post_dash_token(payload)
        if res.ok:
            token = res.json()["token"]
            return jsonify(
                artifactType="dashboard",
                embedUrl=embed_url,
                dashboardId=dashboard_id,
                accessToken=token,
                tokenType="Embed"
            )
        try:
            err_json = res.json()
        except Exception:
            err_json = {"raw": res.text}
        msg = json.dumps(err_json).lower()
        requires_ei = ("requires effective identity" in msg) or ("effective identity" in msg)
        if requires_ei:
            if not ds_list:
                return jsonify(
                    error="Il dataset della dashboard richiede EffectiveIdentity per il bypass, "
                          "ma non è stato possibile rilevare datasetId dai tile.",
                    details=err_json
                ), 400
            bypass_username = username or os.getenv("PBI_RLS_USERNAME") or "embed-bypass@local"
            payload_bypass = {
                "accessLevel": "View",
                "lifetimeInMinutes": TOKEN_TTL_MIN,
                "identities": [{
                    "username": bypass_username,
                    "roles": [BYPASS_ROLE or "AllData"],
                    "datasets": ds_list
                }]
            }
            res2 = post_dash_token(payload_bypass)
            if res2.ok:
                token = res2.json()["token"]
                return jsonify(
                    artifactType="dashboard",
                    embedUrl=embed_url,
                    dashboardId=dashboard_id,
                    accessToken=token,
                    tokenType="Embed"
                )
            try:
                body_err2 = res2.json()
            except Exception:
                body_err2 = {"raw": res2.text}
            return jsonify(error=f"GenerateToken failed [{res2.status_code}] (fallback bypass)", details=body_err2), res2.status_code

        try:
            body_err = res.json()
        except Exception:
            body_err = {"raw": res.text}
        return jsonify(error=f"GenerateToken failed [{res.status_code}]", details=body_err), res.status_code

    # RLS attivo su dashboard
    if not ds_list:
        return jsonify(error="Per RLS su dashboard servono i datasetId dei tile: nessun dataset rilevato."), 400

    payload["identities"] = [{
        "username": username,
        "roles": roles,
        "datasets": ds_list
    }]
    res = post_dash_token(payload)
    if not res.ok:
        try:
            body_err = res.json()
        except Exception:
            body_err = {"raw": res.text}
        return jsonify(error=f"GenerateToken failed [{res.status_code}]", details=body_err), res.status_code

    token = res.json()["token"]
    return jsonify(
        artifactType="dashboard",
        embedUrl=embed_url,
        dashboardId=dashboard_id,
        accessToken=token,
        tokenType="Embed"
    )

if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "5000"))
    app.run(host=host, port=port, debug=os.getenv("FLASK_ENV") == "development")