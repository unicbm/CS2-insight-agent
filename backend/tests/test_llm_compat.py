from app.env_utils import LLMConfig, llm_requests_enabled
from app.llm_compat import normalize_llm_base_url


def test_normalize_strips_chat_completions():
    url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    assert normalize_llm_base_url(url) == "https://open.bigmodel.cn/api/paas/v4"


def test_llm_config_validator():
    cfg = LLMConfig(
        model="glm-4.6v",
        base_url="https://open.bigmodel.cn/api/paas/v4/chat/completions",
    )
    assert cfg.base_url == "https://open.bigmodel.cn/api/paas/v4"


def test_llm_runtime_gate_is_off_even_when_credentials_exist():
    assert llm_requests_enabled(False, LLMConfig(api_key="configured")) is False


def test_llm_runtime_gate_accepts_remote_key_or_keyless_local_endpoint():
    assert llm_requests_enabled(True, LLMConfig(api_key="configured")) is True
    assert llm_requests_enabled(
        True,
        LLMConfig(base_url="http://127.0.0.1:11434/v1"),
    ) is True
