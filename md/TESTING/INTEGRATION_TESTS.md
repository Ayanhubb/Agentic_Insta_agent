# Integration tests

Related: [Testing](TESTING.md), [Instagram API](../API/INSTAGRAM_API.md).

## Mocked integration

These run in the default `pytest` selection. They use fake HTTP or in-memory stores.

| File | What it checks |
| --- | --- |
| `tests/test_instagram_agent_integration.py` | Agent tools through to a counted post. Scheduler does not double-count |
| `tests/test_instagram_media.py` | Graph `POST /media` and `POST /media_publish` against a fake transport |
| `tests/test_instagram_verifier.py` | A 200 response is not treated as verified publication |
| `tests/test_integration_wiring.py` | App wires LLM, image, festival MCP, and vision factories |
| `tests/test_platform_flows.py` | Login through automation and generation with mocks |
| `tests/test_content_orchestrator.py` | MCP context, plan, QA, and `published` stays false |
| `tests/test_canva_mcp.py` | OAuth and remote tool calls against a fake Canva MCP |
| `tests/test_mcp.py` | Allowlist, argument checks, cross-tenant asset denial |

## Opt-in live tests

| Marker | File | Requires |
| --- | --- | --- |
| `integration` | `tests/test_instagram_integration.py` | Meta credentials in the environment |
| `real_instagram` | `tests/test_real_instagram.py` | A real professional account and token |
| `real_openai` | `tests/test_real_openai.py` | `OPENAI_API_KEY` and a configured model |
| `real_deepseek` | none | NOT IMPLEMENTED |

Default pytest excludes `real_openai` and `real_deepseek`. `integration` and `real_instagram` are not excluded by the addopts expression. If those modules skip themselves without credentials, the default run still passes. If they do not skip, set the markers explicitly or expect skips/failures when tokens are absent. Check the module for `pytest.importorskip` or `pytest.mark.skipif` before treating a failure as a product bug.

## Publishing tests

Mocked publish tests assert the agent is the caller of `media_publish` and that MCP, DeepSeek, and the content orchestrator source do not contain that path. They do not post to a live Instagram account.

## Tenant tests

`tests/test_database.py` and `tests/test_generation_api.py` create two users and assert one cannot read the other's images, tasks, or catalog rows.
