# Image QA

Related: [DeepSeek](../AI/DEEPSEEK.md), [Image generation](../AI/IMAGE_GENERATION.md), [Scheduler](../AUTOMATION/SCHEDULER.md).

Vision QA is DeepSeek. Status: **IMPLEMENTED — NOT CONFIGURED** while `DEEPSEEK_API_KEY` is empty. Without the key, both checkers below stay structural. That is not a failed QA feature. Two checkers exist.

## Studio (`ContentOrchestrator`)

After generation, the creative client reviews bytes.

- `DeepSeekCreativeClient.review_image` delegates to `DeepSeekVisionProvider` (`understand` / QA prompt, JSON verdict).
- `GroundedCreativeModel.review_image` opens the bytes with Pillow. Failure or either side under 64 pixels → `passed: false`. Otherwise `passed: true`. This is a structural check, not a brand review.

QA attempts are capped at 3. The stored row uses `generated_images.qa_status` and `qa_json` (Alembic `004`).

Failed QA cannot take the automatic publish path. The studio approve route is still an explicit human action.

## Scheduler (`scheduler/image_qa.py`)

`ImageQAService`:

- No vision client → result `passed=true`, provider `structural`. The pipeline still applies the other approval gates.
- Vision client present → `review_image` / `qa_image` / `analyze_image`, whichever the client implements.

`decide_approval` (`scheduler/approval_policy.py`) sets `publish=false` when reasons include `image_qa_failed`, `provider_result_malformed`, `product_reference_missing`, `required_logo_missing`, `offer_cannot_be_verified`, `festival_context_invalid`, or `tenant_ownership_failed`.

Automatic mode also requires `auto_daily_publish` or `auto_festival_publish` for that lane. Otherwise the status stays `PENDING_APPROVAL`.

## Tests

`tests/test_approval_policy.py`, `tests/test_content_orchestrator.py`, `tests/test_image_validator.py` (upload JPEG/PNG rules, separate from creative QA).
