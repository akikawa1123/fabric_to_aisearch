"""
Fabric Data Agent (tvlog_aisearch_agent) を作成する。

AI Search index は preview のため REST での datasource bind は
スキーマ未公開。作成後は Portal UI で index を接続してください。

前提:
    az login 済み
    push_to_aisearch.ipynb で index が作成済み
    grant_search_rbac.py で AAD 認証・Reader ロール付与済み

実行:
    cp .env.example .env   # .env に値を記入
    python create_data_agent.py
"""
from __future__ import annotations

import json
import time
import uuid

import requests
from azure.identity import DefaultAzureCredential

from config import (
    WORKSPACE_NAME,
    AZURE_SEARCH_ENDPOINT,
    AZURE_SEARCH_INDEX_NAME,
    FABRIC_API,
    FABRIC_SCOPE,
)

AGENT_NAME = "tvlog_aisearch_agent"

AI_INSTRUCTIONS = """\
あなたは日本の地上波テレビ番組の放送内容を検索するアシスタントです。
データソースは Azure AI Search のインデックス 'tvlog-paragraph' で、
番組メタデータと書き起こし（段落単位）にハイブリッド + セマンティック検索が掛けられます。

# 動作ルール
1. ユーザーの質問に対し、必ず AI Search データソースを呼び出してから回答してください。
2. 取得結果から番組名・放送日・チャンネル・該当箇所の発言を引用してください。
3. 検索結果が 0 件の場合は「該当する放送は見つかりませんでした」と答えてください。
4. 放送内容と無関係な質問は対象外であることを伝えてください。
"""

SCHEMA_AGENT = "https://developer.microsoft.com/json-schemas/fabric/item/dataAgent/definition/dataAgent/2.1.0/schema.json"
SCHEMA_STAGE = "https://developer.microsoft.com/json-schemas/fabric/item/dataAgent/definition/stageConfiguration/1.0.0/schema.json"


# ─── helpers ────────────────────────────────────────────────────────

def _headers() -> dict:
    token = DefaultAzureCredential().get_token(FABRIC_SCOPE).token
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _b64(obj: dict) -> str:
    import base64
    return base64.b64encode(json.dumps(obj, ensure_ascii=False).encode()).decode()


def get_workspace_id(headers: dict) -> str:
    r = requests.get(f"{FABRIC_API}/workspaces", headers=headers)
    r.raise_for_status()
    for ws in r.json().get("value", []):
        if ws.get("displayName") == WORKSPACE_NAME:
            return ws["id"]
    raise ValueError(f"workspace not found: {WORKSPACE_NAME}")


def get_or_create_agent(ws_id: str, headers: dict) -> str:
    r = requests.get(f"{FABRIC_API}/workspaces/{ws_id}/items?type=DataAgent", headers=headers)
    r.raise_for_status()
    for item in r.json().get("value", []):
        if item.get("displayName") == AGENT_NAME:
            print(f"  Data Agent already exists: {item['id']}")
            return item["id"]

    print(f"  Creating Data Agent '{AGENT_NAME}' ...")
    r = requests.post(
        f"{FABRIC_API}/workspaces/{ws_id}/items",
        headers=headers,
        json={"displayName": AGENT_NAME, "type": "DataAgent"},
    )
    if r.status_code == 202:
        op_url = r.headers.get("Location")
        for _ in range(60):
            time.sleep(3)
            data = requests.get(op_url, headers=headers).json()
            if data.get("status") == "Succeeded":
                items = requests.get(
                    f"{FABRIC_API}/workspaces/{ws_id}/items?type=DataAgent", headers=headers
                ).json().get("value", [])
                for it in items:
                    if it.get("displayName") == AGENT_NAME:
                        return it["id"]
                raise RuntimeError(f"agent not found after create: {data}")
            if data.get("status") == "Failed":
                raise RuntimeError(f"create failed: {data}")
        raise TimeoutError("create timeout")
    r.raise_for_status()
    return r.json()["id"]


def main() -> None:
    headers  = _headers()
    ws_id    = get_workspace_id(headers)
    agent_id = get_or_create_agent(ws_id, headers)

    portal_url = f"https://app.fabric.microsoft.com/groups/{ws_id}/aiskills/{agent_id}"
    print(f"\n✓ Data Agent ready: {agent_id}")
    print(f"  Portal: {portal_url}")
    print()
    print("次のステップ: Portal で AI Search index を接続してください")
    print("  [Data] タブ → [Add AI Search Index]")
    print(f"  Resource URL : {AZURE_SEARCH_ENDPOINT}")
    print(f"  Index name   : {AZURE_SEARCH_INDEX_NAME}")
    print("  Search Type  : vector + semantic hybrid")
    print("  Semantic config: default")
    print("  Top K        : 8")
    print()
    print("接続後に update_data_agent.py を実行すると AI Instructions が書き込まれます。")


if __name__ == "__main__":
    main()
