from backend.services.llm_quota_guard import is_llm_budget_error, is_llm_transient_error


def test_only_terminal_provider_errors_trigger_global_pause():
    assert is_llm_budget_error("HTTP 429: rate limit exceeded") is True
    assert is_llm_budget_error("insufficient_quota: account balance is exhausted") is True
    assert is_llm_budget_error("invalid api key") is True

    assert is_llm_budget_error("Request timed out after 120 seconds") is False
    assert is_llm_budget_error("503 service unavailable") is False
    assert is_llm_budget_error("connection reset by peer") is False


def test_transient_provider_errors_are_retryable_without_global_pause():
    assert is_llm_transient_error("Request timed out after 120 seconds") is True
    assert is_llm_transient_error("503 service unavailable") is True
    assert is_llm_transient_error("connection reset by peer") is True
    assert is_llm_transient_error("invalid api key") is False
