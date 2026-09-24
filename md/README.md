# Documentation

Authoritative documentation for Instagram Agentic AI. These pages describe the working tree, not an older design.

- Last documentation verification date: **2026-09-24**
- FastAPI `version`: **2.0.0** (`api/app.py`)
- Frontend package version: **1.0.0** (`frontend/package.json`)
- Git HEAD at verification: **`8ad7dc4`**

## Current status

These behaviors are in the code. Empty keys and missing uploads are configuration, not failed features.

| Area | Status |
| --- | --- |
| DeepSeek reasoning, trend analysis, and content planning | **IMPLEMENTED — NOT CONFIGURED.** `DEEPSEEK_API_KEY` is empty. Startup uses `MockLLMProvider` and `GroundedCreativeModel`. A live call raises `DEEPSEEK_CONFIGURATION_ERROR` |
| OpenAI image generation and editing | **IMPLEMENTED — NOT CONFIGURED.** `OPENAI_API_KEY` is empty. Startup uses `MockImageGenerationProvider`. A live call raises `OPENAI_CONFIGURATION_ERROR` |
| Product image and company logo | **IMPLEMENTED — NOT CONFIGURED.** MCP can pass owned files into the OpenAI edit path. Production logos and product images are not uploaded yet. A missing file is not replaced with a generated logo |
| Instagram intelligence | **IMPLEMENTED.** Meta Graph reads go through account intelligence, then MCP, then DeepSeek trend analysis |
| Canva MCP | **IMPLEMENTED — NOT CONFIGURED.** Creative workflow only. `CANVA_ENABLED` defaults to false. Canva never publishes |
| Instagram publishing | **IMPLEMENTED.** The Instagram Agent is the only Meta publisher |
| Global `META_ACCESS_TOKEN` | Not a production credential. Used only when `APP_ENV` is `development`, `dev`, or `local` and `INSTAGRAM_LEGACY_ENV_FALLBACK=true`. Unset `APP_ENV` is production, so the fallback is off |

## Architecture

- [Architecture](ARCHITECTURE.md)
- [AI architecture](AI_ARCHITECTURE.md)
- [MCP overview](MCP.md)

## AI

- [DeepSeek](AI/DEEPSEEK.md)
- [OpenAI](AI/OPENAI.md)
- [Image generation](AI/IMAGE_GENERATION.md)

## Agents

- [Content agent](AGENTS/CONTENT_AGENT.md)
- [Instagram agent](AGENTS/INSTAGRAM_AGENT.md)
- [Trend intelligence agent](AGENTS/TREND_INTELLIGENCE_AGENT.md)
- [Account intelligence agent](AGENTS/ACCOUNT_INTELLIGENCE_AGENT.md)

## MCP

- [MCP architecture](MCP/MCP_ARCHITECTURE.md)
- [Canva](MCP/CANVA_MCP.md)
- [Trend MCP](MCP/TREND_MCP.md)
- [Instagram account MCP](MCP/INSTAGRAM_MCP.md)
- [Product MCP](MCP/PRODUCT_MCP.md)
- [Brand and asset MCP](MCP/BRAND_ASSET_MCP.md)
- [Festival MCP](MCP/FESTIVAL_MCP.md)

## Intelligence

- [Trend research](INTELLIGENCE/TREND_RESEARCH.md)
- [Instagram intelligence](INTELLIGENCE/INSTAGRAM_INTELLIGENCE.md)
- [Trend intelligence architecture](INTELLIGENCE/TREND_INTELLIGENCE_ARCHITECTURE.md)
- [Content opportunities](INTELLIGENCE/CONTENT_OPPORTUNITIES.md)

## Content

- [Content generation](CONTENT/CONTENT_GENERATION.md)
- [Brand assets](CONTENT/BRAND_ASSETS.md)
- [Products](CONTENT/PRODUCTS.md)
- [Image QA](CONTENT/IMAGE_QA.md)

## Automation

- [Scheduler](AUTOMATION/SCHEDULER.md)
- [Festival automation](AUTOMATION/FESTIVAL_AUTOMATION.md)
- [Daily automation](AUTOMATION/DAILY_AUTOMATION.md)

## Database

- [Database](DATABASE/DATABASE.md)
- [Schema](DATABASE/DATABASE_SCHEMA.md)
- [Migrations](DATABASE/MIGRATIONS.md)

## API

- [API reference](API/API_REFERENCE.md)
- [Authentication](API/AUTHENTICATION.md)
- [Instagram API](API/INSTAGRAM_API.md)

## Frontend

- [Frontend](FRONTEND/FRONTEND.md)

## Storage

- [Media storage](STORAGE/MEDIA_STORAGE.md)

## Security

- [Security](SECURITY/SECURITY.md)
- [Secrets](SECURITY/SECRETS.md)
- [Tenant isolation](SECURITY/TENANT_ISOLATION.md)

## Testing

- [Testing](TESTING/TESTING.md)
- [Integration tests](TESTING/INTEGRATION_TESTS.md)

## Reports

- [Final implementation report](REPORTS/FINAL_IMPLEMENTATION_REPORT.md)
- [Final integration audit](REPORTS/FINAL_INTEGRATION_AUDIT.md)
- [Changelog](REPORTS/CHANGELOG.md)
- [Documentation audit](REPORTS/FINAL_DOCUMENTATION_AUDIT.md)

## Install

Python 3.11+ (Docker image uses 3.12) and Node 20+.

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
cd frontend
npm install
```

Generate `TOKEN_ENCRYPTION_KEY` with `Fernet.generate_key()` before storing Instagram or Canva tokens. Do not commit `.env`.

Default pytest excludes `real_openai` and `real_deepseek`:

```bash
python -m pytest
cd frontend && npm test
```
