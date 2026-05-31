"""
push_to_aisearch.ipynb を Fabric ワークスペースへアップロードする。

- 既存ノートブックがあれば updateDefinition で上書き (idempotent)
- default_lakehouse メタデータを Lakehouse ID で注入

前提:
    az login 済み
    cp .env.example .env  → .env に値を記入

実行:
    python deploy_notebook.py
"""
from __future__ import annotations

import base64
import json
import os
import time

import requests
from azure.identity import DefaultAzureCredential

from config import WORKSPACE_NAME, LAKEHOUSE_NAME, FABRIC_API, FABRIC_SCOPE

NOTEBOOK_PATH = os.path.join(os.path.dirname(__file__), "push_to_aisearch.ipynb")
NOTEBOOK_NAME = "push_to_aisearch"


# ─── helpers ────────────────────────────────────────────────────────

def _headers() -> dict:
    token = DefaultAzureCredential().get_token(FABRIC_SCOPE).token
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _poll_lro(location: str, headers: dict, timeout: int = 300) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = requests.get(location, headers=headers)
        r.raise_for_status()
        body = r.json()
        if body.get("status") in ("Succeeded", "Failed"):
            return body
        time.sleep(3)
    raise TimeoutError("LRO timed out")


def _get_item_id(ws_id: str, name: str, item_type: str, headers: dict) -> str | None:
    r = requests.get(
        f"{FABRIC_API}/workspaces/{ws_id}/items?type={item_type}", headers=headers
    )
    r.raise_for_status()
    for item in r.json().get("value", []):
        if item.get("displayName") == name:
            return item["id"]
    return None


# ─── main ────────────────────────────────────────────────────────────

def resolve_ids(headers: dict) -> tuple[str, str]:
    r = requests.get(f"{FABRIC_API}/workspaces", headers=headers)
    r.raise_for_status()
    ws_id = next(
        ws["id"] for ws in r.json()["value"] if ws["displayName"] == WORKSPACE_NAME
    )
    lh_id = _get_item_id(ws_id, LAKEHOUSE_NAME, "Lakehouse", headers)
    if lh_id is None:
        raise RuntimeError(f"Lakehouse '{LAKEHOUSE_NAME}' not found in workspace '{WORKSPACE_NAME}'")
    return ws_id, lh_id


def build_payload(ws_id: str, lh_id: str) -> str:
    with open(NOTEBOOK_PATH, "r", encoding="utf-8-sig") as f:
        nb = json.load(f)
    nb.setdefault("metadata", {})
    nb["metadata"]["dependencies"] = {
        "lakehouse": {
            "default_lakehouse": lh_id,
            "default_lakehouse_name": LAKEHOUSE_NAME,
            "default_lakehouse_workspace_id": ws_id,
        }
    }
    return base64.b64encode(json.dumps(nb).encode("utf-8")).decode("utf-8")


def deploy() -> str:
    headers = _headers()
    ws_id, lh_id = resolve_ids(headers)
    print(f"workspace_id = {ws_id}")
    print(f"lakehouse_id = {lh_id}")

    payload    = build_payload(ws_id, lh_id)
    definition = {
        "format": "ipynb",
        "parts": [{"path": "artifact.content.ipynb", "payload": payload, "payloadType": "InlineBase64"}],
    }
    body = {"displayName": NOTEBOOK_NAME, "type": "Notebook", "definition": definition}

    resp = requests.post(f"{FABRIC_API}/workspaces/{ws_id}/items", headers=headers, json=body)

    if resp.status_code == 202:
        nb_id = _poll_lro(resp.headers["Location"], headers).get("resourceId") or \
                _get_item_id(ws_id, NOTEBOOK_NAME, "Notebook", headers)
    elif resp.status_code in (200, 201):
        nb_id = resp.json()["id"]
    elif resp.status_code == 409:
        print("既存ノートブックを更新します...")
        nb_id = _get_item_id(ws_id, NOTEBOOK_NAME, "Notebook", headers)
        upd = requests.post(
            f"{FABRIC_API}/workspaces/{ws_id}/items/{nb_id}/updateDefinition",
            headers=headers,
            json={"definition": definition},
        )
        if upd.status_code == 202:
            _poll_lro(upd.headers["Location"], headers)
        elif upd.status_code not in (200,):
            upd.raise_for_status()
    else:
        resp.raise_for_status()
        nb_id = resp.json()["id"]

    print(f"\nデプロイ完了: notebook_id = {nb_id}")
    print(f"Portal: https://app.fabric.microsoft.com/groups/{ws_id}/synapsenotebooks/{nb_id}")
    return nb_id


if __name__ == "__main__":
    deploy()
