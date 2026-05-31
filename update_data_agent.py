"""
Fabric Data Agent に AI Instructions と検索設定 (description / topk) を書き込む。

Portal で AI Search index を接続した後に実行してください。

前提:
    az login 済み
    create_data_agent.py 実行済み
    Portal で tvlog_aisearch_agent に AI Search index を接続済み

実行:
    cp .env.example .env   # .env に値を記入
    python update_data_agent.py
"""
from __future__ import annotations

import base64
import json
import time

import requests
from azure.identity import DefaultAzureCredential

from config import WORKSPACE_NAME, FABRIC_API, FABRIC_SCOPE

AGENT_NAME = "tvlog_aisearch_agent"

AI_INSTRUCTIONS = """\
あなたは日本の地上波テレビ番組（NHK 総合、NHK E、日テレ、テレビ朝日、TBS、テレビ東京、フジテレビなど）の
放送内容を検索するアシスタントです。データソースは Azure AI Search のインデックス `tvlog-paragraph` で、
番組メタデータと書き起こし（段落単位）にハイブリッド + セマンティック検索が掛けられます。

# 動作ルール
1. ユーザーの質問に対し、必ず AI Search データソース `tvlog-paragraph` を呼び出してから回答してください。
   勝手に知識から答えてはいけません。
2. 検索クエリは日本語キーワードを優先し、放送局名・番組名・日付・出演者・トピックを抽出して投入します。
3. 取得結果から **番組名・放送日時・チャンネル・該当箇所の発言** を必ず引用元として示してください。
4. 引用は箇条書きで `- 【番組名】(放送日 局名) 「発言抜粋」` の形式で出してください。
5. 検索結果が 0 件の場合は推測せず「該当する放送は見つかりませんでした」と明確に答えてください。
6. 個人のプライバシーに踏み込むコメントや、政治・宗教の評価はしないでください。事実の引用に留めます。
7. 質問が放送内容と無関係な場合は丁重に対象外であることを伝えてください。

# インデックスのフィールド
- para_id          : 段落の一意 ID (キー)
- broadcast_date   : 放送日 (YYYY-MM-DD 形式)。filterable / facetable
- station_code     : 放送局コード。filterable / facetable
- program_name     : 番組名。全文検索可 / filterable
- corner_name      : コーナー名。全文検索可 / filterable
- topic_name       : トピック名。全文検索可 / filterable
- topic_category   : トピックカテゴリ。filterable / facetable
- topic_person     : 登場人物名 Collection(String)。filterable / facetable
                     ※ OData any() フィルタで完全一致検索が可能
- persons_text     : topic_person を空白区切りで連結したテキスト (セマンティック検索用)
- paragraph_text   : 本文テキスト (ja.microsoft アナライザー)
- paragraph_vector : 本文の embedding ベクトル (1536 次元)

# 人物名での検索について
- 人物名検索は OData $filter を使って正確に検索してください。
  例: $filter=topic_person/any(p: p eq '山田太郎')
- 複数人物の場合は or で繋ぎます:
  例: $filter=topic_person/any(p: p eq '山田太郎') or topic_person/any(p: p eq '鈴木花子')
- persons_text フィールドへのテキスト検索でも人物名クエリに対応できます (セマンティックランキング改善済み)。

# よくある質問パターン
- 「〇〇 について話していた番組は?」→ トピックキーワードで検索し上位 3〜5 件を引用
- 「△月△日の □□（局名 or 番組名）で何があった?」→ 日付 + 局名/番組名でフィルタ
- 「☆☆（人物）が出演していた番組」→ topic_person/any(p: p eq '☆☆') でフィルタ
- 「災害／事件のニュース報道」→ 事象名 + 「ニュース」「速報」等のキーワード併用

# 回答スタイル
- 日本語で簡潔に。要点を 2〜4 文でまとめ、続けて引用リストを出す。
- 表記揺れがあるキーワード（カタカナ／漢字／英字）はクエリを言い換えて再検索してもよい。
"""

SEARCH_DISPLAY_NAME      = "TV 放送書き起こし (tvlog-paragraph)"
SEARCH_USER_DESCRIPTION  = (
    "日本の地上波テレビ番組の書き起こしを段落単位で検索できるインデックスです。"
    "番組名・放送日・チャンネル・発言内容を引用付きで取り出せます。"
)
SEARCH_DESCRIPTION = (
    "テレビ放送（NHK 総合 / NHK E / 日テレ / テレビ朝日 / TBS / テレビ東京 / フジテレビなど）の"
    "書き起こしを段落（paragraph）単位でハイブリッド + セマンティック検索する。"
    "フィールド: para_id(キー), broadcast_date(放送日 YYYY-MM-DD), station_code(局コード), "
    "program_name(番組名), corner_name(コーナー名), topic_name(トピック名), "
    "topic_category(カテゴリ), "
    "topic_person(登場人物名リスト Collection(String) - OData any() フィルタで人物名完全一致検索可能。"
    "例: $filter=topic_person/any(p: p eq '人物名')), "
    "persons_text(人物名連結テキスト・セマンティック検索用), "
    "paragraph_text(本文テキスト), paragraph_vector(embedding ベクトル)。"
    "番組内容・出演者・トピック・特定日付の放送内容に関する質問はこのデータソースを使うこと。"
    "人物名で検索する場合は topic_person フィールドに OData any() フィルタを使うこと。"
)
SEARCH_TOPK = 8


# ─── helpers ────────────────────────────────────────────────────────

def _headers() -> dict:
    token = DefaultAzureCredential().get_token(FABRIC_SCOPE).token
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _lro_wait(location: str, headers: dict, timeout: int = 300) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = requests.get(location, headers=headers)
        r.raise_for_status()
        body = r.json()
        if body.get("status") in ("Succeeded", "Failed"):
            return body
        time.sleep(2)
    raise TimeoutError("LRO timed out")


def get_ids(headers: dict) -> tuple[str, str]:
    ws_list = requests.get(f"{FABRIC_API}/workspaces", headers=headers).json()["value"]
    ws_id   = next(w["id"] for w in ws_list if w["displayName"] == WORKSPACE_NAME)
    items   = requests.get(
        f"{FABRIC_API}/workspaces/{ws_id}/items?type=DataAgent", headers=headers
    ).json()["value"]
    agent_id = next(i["id"] for i in items if i["displayName"] == AGENT_NAME)
    return ws_id, agent_id


def get_definition(ws_id: str, agent_id: str, headers: dict) -> dict:
    r = requests.post(
        f"{FABRIC_API}/workspaces/{ws_id}/items/{agent_id}/getDefinition", headers=headers
    )
    if r.status_code == 202:
        op = _lro_wait(r.headers["Location"], headers)
        if op.get("status") != "Succeeded":
            raise RuntimeError(f"getDefinition failed: {op}")
        rr = requests.get(r.headers["Location"].rstrip("/") + "/result", headers=headers)
        rr.raise_for_status()
        return rr.json()["definition"]
    r.raise_for_status()
    return r.json()["definition"]


def update_definition(ws_id: str, agent_id: str, definition: dict, headers: dict) -> None:
    r = requests.post(
        f"{FABRIC_API}/workspaces/{ws_id}/items/{agent_id}/updateDefinition",
        headers=headers,
        json={"definition": definition},
    )
    if r.status_code == 202:
        op = _lro_wait(r.headers["Location"], headers)
        if op.get("status") != "Succeeded":
            raise RuntimeError(f"updateDefinition failed: {op}")
        print("  updateDefinition: Succeeded")
        return
    r.raise_for_status()
    print(f"  updateDefinition: HTTP {r.status_code}")


def main() -> None:
    headers  = _headers()
    ws_id, agent_id = get_ids(headers)
    print(f"workspace_id = {ws_id}")
    print(f"agent_id     = {agent_id}\n")

    print("[1/3] 現定義を取得 ...")
    definition = get_definition(ws_id, agent_id, headers)
    parts      = definition.get("parts", [])
    print(f"  parts: {len(parts)}")

    stage_part = next(
        (p for p in parts if p.get("path") == "Files/Config/draft/stage_config.json"), None
    )
    if stage_part is None:
        raise RuntimeError("stage_config.json が見つかりません")

    stage = json.loads(base64.b64decode(stage_part["payload"]).decode("utf-8"))
    stage["aiInstructions"] = AI_INSTRUCTIONS

    configs = stage.get("experimental", {}).get("azureAISearchConfigs", [])
    if not configs:
        raise RuntimeError(
            "azureAISearchConfigs が空です。Portal で AI Search index を接続してから再実行してください。"
        )
    for cfg in configs:
        cfg["azureAiSearchDescription"]     = SEARCH_DESCRIPTION
        cfg["azureAiSearchUserDescription"] = SEARCH_USER_DESCRIPTION
        cfg["azureAiSearchDisplayName"]     = SEARCH_DISPLAY_NAME
        cfg["azureAiSearchTopk"]            = SEARCH_TOPK
        print(f"  AI Search index: {cfg.get('azureAiSearchIndexName')} "
              f"(type={cfg.get('azureAiSearchSearchType')}, topk={cfg['azureAiSearchTopk']})")

    stage_part["payload"] = base64.b64encode(
        json.dumps(stage, ensure_ascii=False, indent=2).encode("utf-8")
    ).decode()
    stage_part["payloadType"] = "InlineBase64"

    print("\n[2/3] updateDefinition を送信 ...")
    update_definition(ws_id, agent_id, {"parts": parts}, headers)

    print("\n[3/3] 反映を確認 ...")
    after = get_definition(ws_id, agent_id, headers)
    for p in after.get("parts", []):
        if p["path"] == "Files/Config/draft/stage_config.json":
            s = json.loads(base64.b64decode(p["payload"]).decode("utf-8"))
            print(f"  aiInstructions: {len(s.get('aiInstructions') or '')} chars")
            for c in s.get("experimental", {}).get("azureAISearchConfigs", []):
                print(f"  index={c.get('azureAiSearchIndexName')} "
                      f"displayName={c.get('azureAiSearchDisplayName')!r} "
                      f"topk={c.get('azureAiSearchTopk')}")

    print(f"\n✓ 完了。Portal で reload して Agent Instructions を確認してください:")
    print(f"  https://app.fabric.microsoft.com/groups/{ws_id}/aiskills/{agent_id}")


if __name__ == "__main__":
    main()
