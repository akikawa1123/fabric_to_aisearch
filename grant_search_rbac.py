"""Enable AAD auth on AI Search + assign Search Index Data Reader to current user.

Fabric Data Agent (AI Search preview) calls the search endpoint with the
querying user's Entra token, so the user needs RBAC on the search service.

Steps:
 1. PATCH the Search service to set authOptions = aadOrApiKey
    (so Entra ID tokens are accepted alongside API keys)
 2. Assign "Search Index Data Reader"
    (role GUID: 1407120a-92aa-4202-b7e9-c0e197c71c8f)
    to the current signed-in user on the search service scope

前提:
    az login 済み
    実行ユーザーが Search service の Contributor 以上 (authOptions 変更用)

実行:
    cp .env.example .env   # .env に値を記入
    python grant_search_rbac.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import time

import requests

from config import SUBSCRIPTION_ID, SEARCH_RG, SEARCH_NAME

ROLE_SEARCH_INDEX_DATA_READER = "1407120a-92aa-4202-b7e9-c0e197c71c8f"
API_VERSION = "2023-11-01"


def az(args: list[str]) -> str:
    out = subprocess.check_output(["az", *args], shell=True)
    return out.decode().strip()


def arm_token() -> str:
    return az(["account", "get-access-token", "--resource",
               "https://management.azure.com", "--query", "accessToken", "-o", "tsv"])


def current_user_object_id() -> str:
    return az(["ad", "signed-in-user", "show", "--query", "id", "-o", "tsv"])


def current_user_upn() -> str:
    return az(["ad", "signed-in-user", "show", "--query", "userPrincipalName", "-o", "tsv"])


def main() -> None:
    token = arm_token()
    H = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    base = (
        f"https://management.azure.com/subscriptions/{SUBSCRIPTION_ID}"
        f"/resourceGroups/{SEARCH_RG}/providers/Microsoft.Search/searchServices/{SEARCH_NAME}"
    )

    # --- 1. authOptions = aadOrApiKey ---
    print(f"[1/2] Enable AAD auth on {SEARCH_NAME} ...")
    get_url = f"{base}?api-version={API_VERSION}"
    cur = requests.get(get_url, headers=H).json()
    props = cur.get("properties", {})
    auth  = props.get("authOptions")
    disable_local = props.get("disableLocalAuth")
    print(f"  current authOptions    : {auth}")
    print(f"  current disableLocalAuth: {disable_local}")

    desired = {"aadOrApiKey": {"aadAuthFailureMode": "http401WithBearerChallenge"}}
    if auth != desired or disable_local:
        patch = {"properties": {"authOptions": desired, "disableLocalAuth": False}}
        r = requests.patch(get_url, headers=H, data=json.dumps(patch))
        if not r.ok:
            print(r.status_code, r.text)
            sys.exit(1)
        print("  -> updated to aadOrApiKey")
        for _ in range(30):
            time.sleep(2)
            s = requests.get(get_url, headers=H).json().get("properties", {}).get("provisioningState")
            if s == "succeeded":
                break
            print(f"  provisioningState={s} ...")
    else:
        print("  -> already configured")

    # --- 2. Role assignment: Search Index Data Reader ---
    user_oid = current_user_object_id()
    upn      = current_user_upn()
    print(f"\n[2/2] Assign 'Search Index Data Reader' to {upn} ({user_oid}) ...")
    scope = (
        f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{SEARCH_RG}"
        f"/providers/Microsoft.Search/searchServices/{SEARCH_NAME}"
    )
    cmd = [
        "role", "assignment", "create",
        "--assignee-object-id", user_oid,
        "--assignee-principal-type", "User",
        "--role", ROLE_SEARCH_INDEX_DATA_READER,
        "--scope", scope,
    ]
    try:
        out = az(cmd)
        print("  -> assigned")
        print(out[:200])
    except subprocess.CalledProcessError as e:
        msg = (e.output or b"").decode(errors="ignore")
        if "RoleAssignmentExists" in msg or "already exists" in msg.lower():
            print("  -> already assigned")
        else:
            print(msg)
            raise

    print("\n✓ 完了. RBAC 反映に数分かかる場合があります。")
    print("  追加ユーザーへのロール付与:")
    print(f"  az role assignment create --assignee <upn-or-object-id> "
          f"--role {ROLE_SEARCH_INDEX_DATA_READER} --scope {scope}")


if __name__ == "__main__":
    main()
