# fabric-to-aisearch

Fabric Lakehouse の Bronze テーブルを Azure AI Search にベクトル index として push し、
Fabric Data Agent から自然言語で問い合わせるシナリオのリファレンス実装です。

```
Fabric Lakehouse (Bronze テーブル)
        │  push_to_aisearch.ipynb (Fabric notebook)
        ▼
Azure AI Search  index: tvlog-paragraph
   - integrated vectorizer (AOAI text-embedding-3-small, Search MI 認証)
   - semantic configuration: default
        │
        ▼
Fabric Data Agent  (tvlog_aisearch_agent)
   - hybrid_semantic 検索 + AI Instructions
        │
        ▼  Publish
Microsoft 365 Copilot / Copilot in Fabric / REST API
```

## 前提リソース

| リソース | 説明 |
|---|---|
| Microsoft Fabric ワークスペース | Lakehouse + Data Agent を作成する場所 |
| Fabric Lakehouse | Bronze テーブル (`tvlog_dummydata_1000`) 取り込み済み |
| Azure AI Search | **Basic 以上**のティア (integrated vectorizer + セマンティック検索にはベクトル検索対応が必要) |
| Azure OpenAI (Azure AI Foundry) | `text-embedding-3-small` デプロイ済み。[Azure AI Foundry](https://ai.azure.com) から作成・デプロイを推奨 |
| Azure CLI (`az`) | ローカルに `az login` 済み |

---

## Azure AI Search のデプロイ

> 既に AI Search リソースが作成済みの場合はこのセクションをスキップしてください。

### ティア選択

| ティア (SKU) | ベクトル検索 | Semantic Ranker 利用可否 | 推奨用途 |
|---|:---:|:---:|---|
| Free | ✗ | ✗ | 動作確認不可 (ベクトル非対応) |
| **Basic** | ✓ | ✓ | 開発・PoC 用途 (最小構成) |
| Standard S1 | ✓ | ✓ | 本番・大規模インデックス |
| Standard S2/S3 | ✓ | ✓ | 高スループット要件 |

integrated vectorizer と Semantic Ranker は **Basic 以上の SKU** が必要です。

> **「Semantic Ranker の課金プラン」と「サービス SKU の Free」は別物です**  
> Semantic Ranker には独自の課金プランがあり、`--semantic-search free`（月 1,000 クエリ無料）と  
> `--semantic-search standard`（従量課金）の 2 種類があります。  
> これはサービス SKU の「Free ティア」とは無関係です。Basic 以上の SKU であれば、どちらの課金プランでも Semantic Ranker が利用できます。

### Azure Portal で作成する場合

1. [Azure Portal](https://portal.azure.com) → **[リソースの作成]** → `Azure AI Search` を検索
2. **[作成]** をクリックし以下を入力:

   | 項目 | 設定値 |
   |---|---|
   | サブスクリプション | 使用するサブスクリプション |
   | リソース グループ | Fabric/AOAI と同じグループを推奨 |
   | サービス名 | 任意 (例: `myproject-search`) |
   | 場所 | AOAI と同じリージョンを推奨 (ネットワーク遅延・通信コスト低減) |
   | 価格レベル | **Basic** 以上 |

3. **[確認および作成]** → **[作成]**

4. デプロイ完了後、リソースの **[概要]** ページで以下を控える:
   - **URL** (例: `https://myproject-search.search.windows.net`) → `.env` の `AZURE_SEARCH_ENDPOINT`
   - **[キー]** → **[管理者キー]** のプライマリキー → `.env` の `AZURE_SEARCH_ADMIN_KEY`

5. **[設定] → [ID]** を開き、**システム割り当てマネージド ID** を **オン** にして保存する  
   (integrated vectorizer が AOAI を呼ぶために必要)

6. **[設定] → [プレミアム機能]** を開き、**「Free (月 1,000 クエリ無料)」** または **「Standard (従量課金)」** を有効にする  
   (Semantic Ranker を使うために必要。サービス SKU の「Free ティア」とは別の設定です)

### Azure CLI で作成する場合

```bash
# 変数を設定
SEARCH_NAME="myproject-search"
RESOURCE_GROUP="myproject-rg"
LOCATION="japaneast"   # AOAI と同じリージョンを推奨

# AI Search サービス作成
az search service create \
  --name $SEARCH_NAME \
  --resource-group $RESOURCE_GROUP \
  --location $LOCATION \
  --sku Basic \
  --identity-type SystemAssigned

# admin key を取得
az search admin-key show \
  --service-name $SEARCH_NAME \
  --resource-group $RESOURCE_GROUP \
  --query primaryKey -o tsv

# Semantic Ranker を有効化 (無料枠プラン: 月 1,000 クエリまで無料)
# ※ ここの「free」はサービス SKU の Free ティアではなく、Semantic Ranker 機能の課金プラン名
az search service update \
  --name $SEARCH_NAME \
  --resource-group $RESOURCE_GROUP \
  --semantic-search free
```

> **`--semantic-search` の値について**  
> `free` と `standard` は Semantic Ranker 機能自体の**課金プラン名**です。サービス SKU（Basic/Standard など）とは独立した設定です。  
> - `free` — 月 1,000 クエリまで無料。開発・PoC 用途に適しています。  
> - `standard` — 従量課金。本番環境など大量クエリが予想される場合に使用します。  
> どちらのプランでも、サービス SKU が **Basic 以上**であれば利用できます。

### setup_mi_rbac.py との関係

`setup_mi_rbac.py` は AI Search の **Managed Identity** (上記手順 5 で有効化) が  
AOAI の Embedding API を呼べるよう `Cognitive Services User` ロールを付与します。  
AI Search サービス作成後、`setup_mi_rbac.py` を実行する前に必ず MI を有効にしてください。

---

## Azure OpenAI (Azure AI Foundry) のデプロイ

> 既に `text-embedding-3-small` がデプロイ済みの場合はスキップしてください。

### Azure AI Foundry でのデプロイ手順 (推奨)

現在は Azure OpenAI リソースの作成・モデルのデプロイは  
[Azure AI Foundry](https://ai.azure.com) から行うのが標準的な方法です。

1. [Azure AI Foundry](https://ai.azure.com) にアクセスしてサインイン
2. **[+ プロジェクトを作成する]** → ハブを新規作成 (または既存ハブを選択)
   - ハブの作成時に Azure OpenAI リソースが自動的にプロビジョニングされます
3. プロジェクト内の **[モデルカタログ]** → `text-embedding-3-small` を検索 → **[デプロイ]**

   | 項目 | 設定値 |
   |---|---|
   | デプロイ名 | `text-embedding-3-small` (任意。`.env` の `AOAI_EMBEDDING_DEPLOYMENT` に合わせる) |
   | デプロイの種類 | Standard または Global Standard |

4. デプロイ完了後、**[プロジェクトの設定] → [接続済みリソース]** で Azure OpenAI リソースを選択し  
   以下を控える:
   - **エンドポイント** (例: `https://YOUR-HUB.openai.azure.com`) → `.env` の `AOAI_ENDPOINT`
   - リソース名 → `.env` の `AZURE_AOAI_ACCOUNT`
   - リソースが属するリソースグループ → `.env` の `AZURE_AOAI_RG`

### Azure CLI でのデプロイ

```bash
# Azure OpenAI リソース作成
az cognitiveservices account create \
  --name YOUR-AOAI-NAME \
  --resource-group YOUR-RG \
  --location japaneast \
  --kind OpenAI \
  --sku S0

# text-embedding-3-small をデプロイ
az cognitiveservices account deployment create \
  --name YOUR-AOAI-NAME \
  --resource-group YOUR-RG \
  --deployment-name text-embedding-3-small \
  --model-name text-embedding-3-small \
  --model-version "1" \
  --model-format OpenAI \
  --sku-capacity 100 \
  --sku-name Standard
```

> **リージョンに関する注意**  
> `text-embedding-3-small` が利用可能なリージョンは限られています。  
> 詳細は [Azure OpenAI モデルの可用性](https://learn.microsoft.com/azure/ai-services/openai/concepts/models) を確認してください。  
> また、AI Search の integrated vectorizer は **AI Search と同じリージョン** の AOAI エンドポイントを参照することを推奨します (レイテンシ・コスト低減)。

---

## セットアップ

### 1. リポジトリをクローン・依存インストール

```bash
git clone https://github.com/akikawa1123/fabric-to-aisearch.git
cd fabric-to-aisearch
pip install -r requirements.txt
```

### 2. 設定ファイルを用意

```bash
cp .env.example .env
```

`.env` を開き、各項目に自分の環境の値を記入してください。
`.env` は `.gitignore` に含まれているためコミットされません。

### 3. AI Search の Managed Identity セットアップ (1 回のみ)

integrated vectorizer が動くには、AI Search サービス自体が AOAI を呼べる必要があります。

```bash
python setup_mi_rbac.py
```

実行ユーザーに必要なロール:
- AI Search service の **Contributor** 以上 (MI 有効化用)
- AOAI account の **User Access Administrator** または **Owner** (ロール割当用)

### 4. notebook を Fabric にデプロイして実行

```bash
python deploy_notebook.py
```

Fabric portal で `push_to_aisearch` notebook を開き **Run all** を実行します。
完了すると AI Search に index `tvlog-paragraph` が作成され、データが upload されます。

> **ノートブック内のパラメータ設定**
> notebook を開いたら **セル 1 (パラメータ)** に移動して以下を書き換えてください:
> - `AZURE_SEARCH_ENDPOINT` — AI Search のエンドポイント URL
> - `AZURE_SEARCH_ADMIN_KEY` — AI Search の admin key
>   (本番では Key Vault + `notebookutils.credentials.getSecret()` を推奨)
> - `AOAI_ENDPOINT` — Azure OpenAI のエンドポイント URL

### 5. Fabric Data Agent を作成

```bash
python create_data_agent.py
```

`tvlog_aisearch_agent` が作成されます。

### 6. Portal で AI Search index を Data Agent に接続

> AI Search datasource の接続は preview 機能のため、REST API スキーマが未公開です。
> このステップのみ Portal UI で行います。

[Fabric Portal](https://app.fabric.microsoft.com/) → `tvlog_aisearch_agent` を開き
**[Data] → [Add AI Search Index]** で以下を設定して **Save**:

| 項目 | 値 |
|---|---|
| Resource URL | `https://<your-search-service>.search.windows.net` |
| Index name | `tvlog-paragraph` |
| Search Type | Hybrid + Semantic |
| Semantic configuration | `default` |
| Top K | `8` |

### 7. RBAC と AI Instructions を設定

```bash
python grant_search_rbac.py   # AAD 認証許可 + 実行ユーザーに Reader ロール付与
python update_data_agent.py   # AI Instructions / 索引説明を Data Agent に書き込み
```

`grant_search_rbac.py` の効果:
- AI Search サービスの認証モードを `aadOrApiKey` に設定
- 実行ユーザーに `Search Index Data Reader` ロールを付与

> 他のユーザーが Data Agent を使う場合も同様に `Search Index Data Reader` の付与が必要です:
> ```bash
> az role assignment create \
>   --assignee <user-upn-or-object-id> \
>   --role "1407120a-92aa-4202-b7e9-c0e197c71c8f" \
>   --scope /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Search/searchServices/<name>
> ```

## ファイル構成

| ファイル | 役割 | 実行場所 |
|---|---|---|
| `push_to_aisearch.ipynb` | Lakehouse → AI Search の embed & push 本体 | Fabric notebook |
| `deploy_notebook.py` | notebook を Fabric にアップロード | ローカル |
| `setup_mi_rbac.py` | AI Search MI 有効化 + AOAI への RBAC 付与 | ローカル |
| `create_data_agent.py` | Fabric Data Agent 作成 | ローカル |
| `grant_search_rbac.py` | Search サービスを AAD 認証許可 + Reader ロール付与 | ローカル |
| `update_data_agent.py` | Data Agent に AI Instructions / 索引説明を書き込み | ローカル |
| `config.py` | `.env` から設定を読み込む共通モジュール | — |
| `.env.example` | 設定パラメータのテンプレート | — |
| `requirements.txt` | Python 依存パッケージ | — |

## セキュリティに関する注意

- `.env` ファイルは絶対にコミットしないでください (`.gitignore` で除外済み)
- AI Search の admin key は本番環境では Azure Key Vault に保管し、
  Fabric notebook からは `notebookutils.credentials.getSecret()` 経由で取得してください
- RBAC は最小権限原則に従い、各ユーザーに必要なロールのみを付与してください

## Bronze テーブルのスキーマ

| フィールド | 型 | 説明 |
|---|---|---|
| `para_id` | string (PK) | 段落ユニーク ID |
| `date` | string | 放送日 (YYYY-MM-DD) |
| `station` | string | 局コード (1=NHK 総合, 2=テレビ朝日, 3=TBS, 4=日本テレビ, 5=テレビ東京, 6=フジテレビ) |
| `program_name` | string | 番組名 |
| `corner_name` | string | コーナー名 |
| `topic_name` | string | トピック名 |
| `topic_category` | string | トピックカテゴリ |
| `topic_person` | string | 関連人物 (**バックスラッシュ `\` 区切り**で複数人物が入る場合あり。例: `"山田太郎\鈴木花子"`) |
| `paragraph` | string | 書き起こし本文 (段落) |

## AI Search インデックス スキーマ (`tvlog-paragraph`)

`push_to_aisearch.ipynb` が作成するインデックスのフィールド定義です。

| フィールド | 型 | アナライザー | セマンティック | 説明 |
|---|---|---|---|---|
| `para_id` | `String` (key) | — | — | 段落の一意 ID。filterable |
| `broadcast_date` | `String` | — | — | 放送日 (YYYY-MM-DD)。filterable / facetable |
| `station_code` | `String` | — | — | 放送局コード。filterable / facetable |
| `program_name` | `String` | standard | **title** | 番組名。全文検索可 / filterable |
| `corner_name` | `String` | standard | keywords | コーナー名。全文検索可 / filterable |
| `topic_name` | `String` | standard | keywords | トピック名。全文検索可 / filterable |
| `topic_category` | `String` | standard | keywords | トピックカテゴリ。filterable / facetable |
| `topic_person` | `Collection(String)` | **ja.microsoft** | — | 人物名リスト。Bronze の `\` 区切りをリストに変換して格納。OData `any()` フィルタ・facet 対応 |
| `persons_text` | `String` | **ja.microsoft** | **keywords** | `topic_person` を空白区切りで連結した文字列。セマンティックランカーが人物名クエリを正しくスコアリングするために追加 |
| `paragraph_text` | `String` | **ja.microsoft** | **content** | 書き起こし本文 |
| `paragraph_vector` | `Collection(Single)` | — | — | `paragraph_text` の embedding (1536 次元, HNSW) |

### 人物名フィルタ検索

`topic_person` は `Collection(String)` 型のため、**OData `any()` 構文**で完全一致フィルタができます:

```python
# 特定の人物が出演している段落を取得
results = search_client.search(
    search_text="*",
    filter="topic_person/any(p: p eq '山田太郎')",
    select=["para_id", "broadcast_date", "program_name", "topic_person", "paragraph_text"],
)
```

> **なぜ `persons_text` が必要か**  
> `Collection(String)` 型のフィールドはセマンティック検索の `keywords` に指定できません。  
> そのため `persons_text`（String 型・空白区切り連結）を別途用意し、セマンティックランカーが  
> 人物名クエリに対して正しくスコアリングできるようにしています。
