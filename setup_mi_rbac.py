"""
AI Search の system-assigned MI を有効化し、
その MI に AOAI scope で "Cognitive Services OpenAI User" を付与する一回限りのセットアップ。

Fabric ノートブック (push_to_aisearch.ipynb) からは ARM の access token が取得できないため、
このスクリプトはローカル PC または Cloud Shell で実行してください。

前提:
    az login 済み (実行ユーザーに以下のロールが必要)
        - Search service の Contributor 以上 (MI 有効化)
        - AOAI account の User Access Administrator か Owner (ロール割当)

実行:
    pip install requests
    cp .env.example .env   # .env に値を記入
    python setup_mi_rbac.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import uuid

import requests

from config import SUBSCRIPTION_ID, SEARCH_RG, SEARCH_NAME, AOAI_RG, AOAI_ACCOUNT

ROLE_COGNITIVE_SERVICES_OPENAI_USER = "5e0bd9bd-7b93-4f28-af87-19fc36ad61bd"
ARM = "https://management.azure.com"


def _arm_token() -> str:
    out = subprocess.check_output(
        ["az", "account", "get-access-token", "--resource", ARM, "--output", "json"],
        shell=True,
    )
    return json.loads(out)["accessToken"]


def main() -> int:
    if not AOAI_RG or not AOAI_ACCOUNT:
        print("ERROR: AZURE_AOAI_RG and AZURE_AOAI_ACCOUNT must be set in .env")
        return 1

    hdr = {"Authorization": f"Bearer {_arm_token()}", "Content-Type": "application/json"}

    # 1) Search service の MI を有効化 (already enabled -> no-op)
    search_resource_id = (
        f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{SEARCH_RG}"
        f"/providers/Microsoft.Search/searchServices/{SEARCH_NAME}"
    )
    r = requests.get(f"{ARM}{search_resource_id}?api-version=2024-03-01-preview", headers=hdr)
    r.raise_for_status()
    svc = r.json()

    if (svc.get("identity") or {}).get("type") != "SystemAssigned":
        print("enabling system-assigned MI on Search service...")
        r = requests.patch(
            f"{ARM}{search_resource_id}?api-version=2024-03-01-preview",
            headers=hdr,
            json={"identity": {"type": "SystemAssigned"}},
        )
        r.raise_for_status()
        svc = r.json()
    else:
        print("MI already enabled")

    principal_id = svc["identity"]["principalId"]
    print(f"  principalId = {principal_id}")

    # 2) AOAI scope で Cognitive Services OpenAI User を付与 (already assigned -> no-op)
    aoai_scope = (
        f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{AOAI_RG}"
        f"/providers/Microsoft.CognitiveServices/accounts/{AOAI_ACCOUNT}"
    )
    role_def_id = (
        f"/subscriptions/{SUBSCRIPTION_ID}/providers/Microsoft.Authorization"
        f"/roleDefinitions/{ROLE_COGNITIVE_SERVICES_OPENAI_USER}"
    )
    list_url = (
        f"{ARM}{aoai_scope}/providers/Microsoft.Authorization/roleAssignments"
        f"?api-version=2022-04-01&$filter=principalId eq '{principal_id}'"
    )
    r = requests.get(list_url, headers=hdr)
    r.raise_for_status()
    existing = [
        a for a in r.json().get("value", [])
        if a["properties"].get("roleDefinitionId", "").lower().endswith(
            ROLE_COGNITIVE_SERVICES_OPENAI_USER
        )
    ]
    if existing:
        print("role assignment already exists")
    else:
        ra_name = str(uuid.uuid4())
        print(f"creating role assignment {ra_name}...")
        r = requests.put(
            f"{ARM}{aoai_scope}/providers/Microsoft.Authorization"
            f"/roleAssignments/{ra_name}?api-version=2022-04-01",
            headers=hdr,
            json={
                "properties": {
                    "roleDefinitionId": role_def_id,
                    "principalId": principal_id,
                    "principalType": "ServicePrincipal",
                }
            },
        )
        r.raise_for_status()

    print("\n✓ MI / RBAC ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())
