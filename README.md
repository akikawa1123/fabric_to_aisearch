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
| Fabric Lakehouse | Bronze テーブル (`tvlog_sample_1000`) 取り込み済み |
| Azure AI Search | Basic 以上のティア (integrated vectorizer にはベクトル検索が必要) |
| Azure OpenAI | `text-embedding-3-small` デプロイ済み |
| Azure CLI (`az`) | ローカルに `az login` 済み |

## セットアップ

### 1. リポジトリをクローン・依存インストール

```bash
git clone https://github.com/<your-org>/fabric-to-aisearch.git
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
| `topic_person` | string | 関連人物 |
| `paragraph` | string | 書き起こし本文 (段落) |
