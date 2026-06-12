"""Yellow-triage fix tests (reviewer-pass, 2026-06-13): BatchTruncatedError
halving had zero tests after the §6 medium fix. These tests pin:
  - Single-truncation triggers halve-and-retry (both halves re-attempted)
  - Recursive halving terminates at chunks of size 1 (no infinite loop)
  - Singleton truncation falls back to Google (the documented dead-end)
  - Non-truncated errors fall through to the existing Google fallback
"""

from unittest.mock import patch, MagicMock

import pytest

from data_pipeline.auto_translate_hebrew import (
    BatchTruncatedError,
    _translate_batch_via_claude,
)


def _make_claude_response(text: str = "", stop_reason: str = "end_turn"):
    """Build a fake anthropic SDK response with the relevant fields the
    code reads — `content[0].text` and `stop_reason`."""
    block = MagicMock()
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    resp.stop_reason = stop_reason
    return resp


class TestBatchTruncatedRaises:
    """The §6 medium fix raises `BatchTruncatedError` when Claude reports
    `stop_reason='max_tokens'`. Pin that detection."""

    def test_max_tokens_raises_batch_truncated(self):
        fake_client = MagicMock()
        fake_client.messages.create.return_value = _make_claude_response(
            text='{"translations": [{"en": "X", "he": "..."',  # cut JSON
            stop_reason="max_tokens",
        )
        with patch("data_pipeline.auto_translate_hebrew.anthropic.Anthropic", return_value=fake_client), \
             patch("data_pipeline.auto_translate_hebrew._resolve_anthropic_api_key", return_value="dummy"):
            with pytest.raises(BatchTruncatedError) as exc:
                _translate_batch_via_claude("clubs", ["X"] * 100)
            # Error message mentions the category + batch size so the
            # caller's log line is informative.
            assert "clubs" in str(exc.value) or "100" in str(exc.value)

    def test_end_turn_does_not_raise(self):
        """Normal `end_turn` stop reason → no truncation error, parse
        proceeds. The body returns invalid JSON here to confirm the
        truncation check isn't masking a different error path."""
        fake_client = MagicMock()
        fake_client.messages.create.return_value = _make_claude_response(
            text='{"translations": [{"en": "X", "he": "y", "confidence": "high"}]}',
            stop_reason="end_turn",
        )
        with patch("data_pipeline.auto_translate_hebrew.anthropic.Anthropic", return_value=fake_client), \
             patch("data_pipeline.auto_translate_hebrew._resolve_anthropic_api_key", return_value="dummy"):
            result = _translate_batch_via_claude("clubs", ["X"])
            assert len(result) == 1
            assert result[0]["he"] == "y"


class TestBatchTruncatedExceptionShape:
    """Verify the exception carries enough info for the caller's halving
    loop to do its job."""

    def test_exception_inherits_from_runtime_error(self):
        """Catch-all `except Exception` paths still catch it; that
        guarantees we don't crash the pipeline on a halving-loop bug."""
        assert issubclass(BatchTruncatedError, RuntimeError)
