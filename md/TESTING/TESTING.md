# Testing

Related: [Integration tests](INTEGRATION_TESTS.md).

## Backend

`pytest.ini`:

- `testpaths = tests`
- `asyncio_mode = auto`
- Default addopts: `-q -m "not real_openai and not real_deepseek"`
- Markers: `integration`, `real_instagram`, `real_openai`, `real_deepseek`

Run from the repository root:

```bash
python -m pytest
```

Live calls, opt-in:

```bash
python -m pytest -m real_openai
python -m pytest -m real_instagram
python -m pytest -m integration
```

`real_deepseek` is defined and excluded from the default run. No test is marked with it. A live DeepSeek call is not required. DeepSeek itself is **IMPLEMENTED — NOT CONFIGURED** while `DEEPSEEK_API_KEY` is empty. `real_openai` is the same kind of opt-in for OpenAI and is also excluded by default. Latest default run: 421 passed, 4 skipped (Meta credential gates), 2 deselected (`real_openai`). Frontend: 39 tests passed. `npm run build` succeeded.

## What the default suite covers

| Area | Files |
| --- | --- |
| Instagram agent | `test_agent.py`, `test_agentic_workflow.py`, `test_planner.py`, `test_state.py` |
| Content | `test_content_agent.py`, `test_content_orchestrator.py`, `test_opportunity_engine.py` |
| Providers, mocked | `test_ai_providers.py`, `test_openai_llm.py`, `test_openai_image.py`, `test_backend_openai_image.py`, `test_deepseek.py` |
| Images and storage | `test_image_pipeline.py`, `test_image_validator.py`, `test_image_references.py`, `test_media_storage.py` |
| Auth and API | `test_auth.py`, `test_api.py`, `test_generation_api.py`, `test_platform_flows.py`, `test_errors.py` |
| Database | `test_database.py` |
| MCP and tenant | `test_mcp.py`, `test_mcp_account_intelligence.py`, `test_trend_mcp.py`, `test_brand_assets.py` |
| Scheduler and festivals | `test_scheduler.py`, `test_trend_scheduler.py`, `test_approval_policy.py`, `test_festival_intelligence.py` |
| Trends | `test_trend_research.py`, `test_trend_analysis.py`, `test_trend_storage.py`, `test_trends_api.py` |
| Instagram Graph, mocked | `test_instagram_media.py`, `test_instagram_verifier.py`, `test_instagram_intelligence.py`, `test_instagram_agent_integration.py` |
| Canva, mocked | `test_canva_mcp.py` |
| Wiring | `test_integration_wiring.py` |

## Frontend

```bash
cd frontend
npm test
npm run build
```

Vitest files are listed in [Frontend](../FRONTEND/FRONTEND.md).

### This documentation pass

The default pytest run and `npm test` were executed while these pages were written. Results are in [Documentation audit](../REPORTS/FINAL_DOCUMENTATION_AUDIT.md). `npm run build` was not required to verify Markdown and is not claimed here.
