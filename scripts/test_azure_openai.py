"""Check your Azure OpenAI setup. Run from the repo root after filling in .env:

    pip install -r apps/api/requirements.txt
    python scripts/test_azure_openai.py

Reads AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_DEPLOYMENT (chat),
AZURE_OPENAI_EMBEDDING_DEPLOYMENT (optional) and AZURE_OPENAI_API_VERSION from .env.
Prints only status, never the key.
"""
import json
import sys

from pydantic_settings import BaseSettings, SettingsConfigDict


class S(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_deployment: str = ""
    azure_openai_embedding_deployment: str = ""
    azure_openai_api_version: str = "2024-10-21"


s = S()
missing = [k for k in ("azure_openai_endpoint", "azure_openai_api_key", "azure_openai_deployment")
           if not getattr(s, k)]
if missing:
    sys.exit(f"Missing in .env: {', '.join(m.upper() for m in missing)}")

from openai import AzureOpenAI  # noqa: E402

client = AzureOpenAI(azure_endpoint=s.azure_openai_endpoint, api_key=s.azure_openai_api_key,
                     api_version=s.azure_openai_api_version)

print("Chat deployment:", s.azure_openai_deployment)
try:
    r = client.chat.completions.create(
        model=s.azure_openai_deployment, temperature=0, response_format={"type": "json_object"},
        messages=[{"role": "system", "content": 'Reply with JSON only: {"ok": true}'},
                  {"role": "user", "content": "ping"}])
    print("  OK:", json.loads(r.choices[0].message.content))
except Exception as e:
    print("  FAILED:", type(e).__name__, str(e)[:300])

if s.azure_openai_embedding_deployment:
    print("Embedding deployment:", s.azure_openai_embedding_deployment)
    try:
        e = client.embeddings.create(model=s.azure_openai_embedding_deployment, input="gross profit")
        print("  OK: vector length", len(e.data[0].embedding))
    except Exception as ex:
        print("  FAILED:", type(ex).__name__, str(ex)[:300])
else:
    print("Embedding deployment not set (AZURE_OPENAI_EMBEDDING_DEPLOYMENT) - skipped")
