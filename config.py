"""
Centralized config for all local scripts.
Reads values from .env (or real environment variables).

Copy .env.example → .env and fill in your resource details.
"""
from __future__ import annotations
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=False)
except ImportError:
    pass  # python-dotenv optional — fall back to env vars already set


def _require(key: str) -> str:
    v = os.environ.get(key, "")
    if not v:
        raise RuntimeError(
            f"Environment variable '{key}' is not set. "
            "Copy .env.example to .env and fill in your values."
        )
    return v


# ─── Fabric ────────────────────────────────────────────────
WORKSPACE_NAME = _require("FABRIC_WORKSPACE_NAME")
LAKEHOUSE_NAME = _require("FABRIC_LAKEHOUSE_NAME")

# ─── Azure AI Search ────────────────────────────────────────
AZURE_SEARCH_ENDPOINT   = _require("AZURE_SEARCH_ENDPOINT")
AZURE_SEARCH_INDEX_NAME = os.environ.get("AZURE_SEARCH_INDEX_NAME", "tvlog-paragraph")
AZURE_SEARCH_ADMIN_KEY  = _require("AZURE_SEARCH_ADMIN_KEY")

# ─── Azure OpenAI ────────────────────────────────────────────
AOAI_ENDPOINT             = _require("AOAI_ENDPOINT")
AOAI_EMBEDDING_DEPLOYMENT = os.environ.get("AOAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-small")
AOAI_API_VERSION          = os.environ.get("AOAI_API_VERSION", "2024-08-01-preview")

# ─── ARM (setup_mi_rbac.py / grant_search_rbac.py) ──────────
SUBSCRIPTION_ID = _require("AZURE_SUBSCRIPTION_ID")
SEARCH_RG       = _require("AZURE_SEARCH_RG")
SEARCH_NAME     = _require("AZURE_SEARCH_NAME")
AOAI_RG         = os.environ.get("AZURE_AOAI_RG", "")
AOAI_ACCOUNT    = os.environ.get("AZURE_AOAI_ACCOUNT", "")

# ─── Fabric API constants ────────────────────────────────────
FABRIC_API   = "https://api.fabric.microsoft.com/v1"
FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"
