"""Provider compatibility after removing the legacy MMM package; no network."""
import os
import unittest
from unittest.mock import patch

from calendar_agent.config import llm_settings


class CalendarConfigTests(unittest.TestCase):
    def test_provider_keys_do_not_cross_and_aliases_work(self):
        keys = {'ZHIPU_API_KEY': 'fake-zhipu', 'DASHSCOPE_API_KEY': 'fake-dashscope', 'DEEPSEEK_API_KEY': 'fake-deepseek'}
        for provider, expected in [('glm', 'zhipu'), ('qwen', 'dashscope'), ('deepseek', 'deepseek')]:
            with self.subTest(provider=provider), patch.dict(os.environ, keys | {'LLM_PROVIDER': provider}, clear=True):
                cfg = llm_settings()
                self.assertEqual(cfg.provider, expected)
                self.assertEqual(cfg.api_key, 'fake-' + expected)
                self.assertTrue(cfg.base_url.startswith('https://'))
                self.assertTrue(cfg.model)

    def test_generic_overrides_and_reasoning_opt_out(self):
        with patch.dict(os.environ, {'LLM_PROVIDER': 'glm', 'ZHIPU_API_KEY': 'unused', 'LLM_API_KEY': 'fake-override', 'LLM_MODEL': 'custom-model', 'LLM_BASE_URL': 'https://example.invalid/v1', 'LLM_REASONING_EFFORT': 'none'}, clear=True):
            cfg = llm_settings()
            self.assertEqual((cfg.api_key, cfg.model, cfg.base_url, cfg.reasoning_effort), ('fake-override', 'custom-model', 'https://example.invalid/v1', ''))

    def test_offline_disables_configured_model(self):
        with patch.dict(os.environ, {'MMM_OFFLINE': '1', 'LLM_API_KEY': 'fake-key'}, clear=True):
            self.assertFalse(llm_settings().enabled)

    def test_missing_provider_key_does_not_use_another_provider(self):
        with patch.dict(os.environ, {'LLM_PROVIDER': 'deepseek', 'ZHIPU_API_KEY': 'fake-key'}, clear=True):
            self.assertFalse(llm_settings().enabled)

    def test_provider_specific_overrides(self):
        with patch.dict(os.environ, {'LLM_PROVIDER': 'qwen', 'DASHSCOPE_API_KEY': 'fake-key', 'DASHSCOPE_MODEL': 'custom-qwen', 'DASHSCOPE_BASE_URL': 'https://example.invalid/v1'}, clear=True):
            cfg = llm_settings()
            self.assertEqual((cfg.model, cfg.base_url), ('custom-qwen', 'https://example.invalid/v1'))


if __name__ == '__main__':
    unittest.main()
