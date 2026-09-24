# DeepSeek

Related: [AI architecture](../AI_ARCHITECTURE.md), [OpenAI](OPENAI.md), [Image QA](../CONTENT/IMAGE_QA.md), [Trend research](../INTELLIGENCE/TREND_RESEARCH.md).

DeepSeek is a text and vision client. It does not generate images and it does not publish to Instagram.

## Environment

| Variable | Default | Use |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | empty | Required for live calls. Backend only |
| `DEEPSEEK_MODEL` | `deepseek-flash` | Chat and vision model name |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | Host for `POST /chat/completions` |
| `DEEPSEEK_TIMEOUT_SECONDS` | `30` | HTTP timeout |
| `VISION_PROVIDER` | `deepseek` | Vision factory name. The wired factory is DeepSeek |
| `LLM_PROVIDER` | `openai` | Scheduler LLM. Set to `deepseek` to use `DeepSeekLLMProvider` there |
| `LLM_MAX_ATTEMPTS` | `3` | Structured-output retries |
| `LLM_TEMPERATURE` | `0.4` | Sampling temperature |

The key is never written to the database and is not returned by `GET /api/v1/ai/status`.

## Classes (`ai/llm/deepseek.py`)

`backend/ai/llm/deepseek.py` re-exports these. Callers use `ai.llm.deepseek`.

| Class | Role |
| --- | --- |
| `DeepSeekTransport` / `DeepSeekSession` | HTTP `POST {base}/chat/completions` |
| `DeepSeekLLMProvider` | `LLMProvider` for scheduler content plans when `LLM_PROVIDER=deepseek` |
| `DeepSeekCreativeClient` | Studio planner. `create_plan`, and `review_image` delegated to vision |
| `DeepSeekTrendAnalyst` lives in `backend/trends/analyst.py` | Calls `generate_structured` for a `TrendBrief` |

Structured calls send `response_format: {"type":"json_object"}`. Responses are validated. Rejected JSON is retried with feedback up to `llm_max_attempts`.

## Where it is used

| Path | Condition |
| --- | --- |
| Studio plan | `get_creative_model`: any non-empty `DEEPSEEK_API_KEY`, independent of `LLM_PROVIDER` |
| Scheduler plan | Only when `LLM_PROVIDER=deepseek` and the key and model are set. Otherwise OpenAI or `MockLLMProvider` |
| Trend briefs | `DeepSeekTrendAnalyst.analyze` when the scheduler invokes it. It does not browse the web |
| Vision QA | `ai/vision/deepseek.py` `DeepSeekVisionProvider` |

Missing key on the studio path does not call DeepSeek. `GroundedCreativeModel` writes the plan locally.

## Vision

`DeepSeekVisionProvider` methods: `understand_image`, `answer_image_question`, `review_image`.

Local files are sent as base64 data URLs. Public HTTPS URLs are passed through. The provider does not generate pixels and does not publish.

`scheduler/integrations.resolve_vision` loads `ai.vision.deepseek.get_vision_provider`. If that factory fails, vision stays disabled and `ImageQAService` records a structural pass (`provider=structural`).

## Error handling

Transport and validation failures surface as `AppError` codes used by the OpenAI path as well (`OPENAI_TIMEOUT`, `OPENAI_RATE_LIMITED`, configuration errors) or as analyst rejection of invented facts. Secrets are redacted by `services/logging.py` before they reach logs.

## Tests

`tests/test_deepseek.py` mocks the HTTP client. The `real_deepseek` pytest marker exists in `pytest.ini` and is excluded by default. **No test file uses `@pytest.mark.real_deepseek`.** Status: **NOT IMPLEMENTED** for a live DeepSeek test.
