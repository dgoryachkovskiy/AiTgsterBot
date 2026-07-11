# Day 30. Private Local LLM HTTP Service

## Summary

- generated_at: `2026-07-11T13:33:11+00:00`
- service_url: `http://138.16.168.37:8010`
- web_ui_status: `200`
- web_ui_contains_chat: `True`
- model: `qwen2.5:0.5b`
- quantization: `Q4_K_M`
- parameters: `494.03M`
- raw_ollama_public: `False`
- auth: `Bearer token`
- rate_limit_triggered: `True`
- stability_passed: `5/5`
- max_context_truncated: `True`

## Web UI

- status: `200`
- elapsed_seconds: `0.296`
- contains_title: `True`
- contains_chat: `True`
- visual_theme_ru: `Космический AI-наставник`
- visual_theme: `Космический AI-наставник`
- browser_entry: `http://138.16.168.37:8010/`
- screenshot_path: `C:\Users\pospi\Documents\Codex\day30_private_llm_store\web_ui_screenshot.png`

## Health

```json
{
  "ok": true,
  "service": "day30-private-local-llm",
  "ollama_connected": true,
  "ollama_error": "",
  "model": "qwen2.5:0.5b",
  "raw_ollama_url": "http://127.0.0.1:11434",
  "limits": {
    "rate_limit_per_minute": 10,
    "max_messages": 12,
    "max_input_chars": 12000,
    "num_ctx": 4096,
    "num_predict": 256
  },
  "models_count": 1
}
```

## Models

```json
{
  "ok": true,
  "active_model": "qwen2.5:0.5b",
  "available_models": [
    "qwen2.5:0.5b"
  ],
  "active_model_metadata": {
    "details": {
      "parent_model": "",
      "format": "gguf",
      "family": "qwen2",
      "families": [
        "qwen2"
      ],
      "parameter_size": "494.03M",
      "quantization_level": "Q4_K_M"
    },
    "model_info": {
      "general.architecture": "qwen2",
      "general.base_model.0.name": "Qwen2.5 0.5B",
      "general.base_model.0.organization": "Qwen",
      "general.base_model.0.repo_url": "https://huggingface.co/Qwen/Qwen2.5-0.5B",
      "general.base_model.count": 1,
      "general.basename": "Qwen2.5",
      "general.file_type": 15,
      "general.finetune": "Instruct",
      "general.languages": null,
      "general.license": "apache-2.0",
      "general.license.link": "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct/blob/main/LICENSE",
      "general.parameter_count": 494032768,
      "general.quantization_version": 2,
      "general.size_label": "0.5B",
      "general.tags": null,
      "general.type": "model",
      "qwen2.attention.head_count": 14,
      "qwen2.attention.head_count_kv": 2,
      "qwen2.attention.layer_norm_rms_epsilon": 1e-06,
      "qwen2.block_count": 24,
      "qwen2.context_length": 32768,
      "qwen2.embedding_length": 896,
      "qwen2.feed_forward_length": 4864,
      "qwen2.rope.freq_base": 1000000,
      "tokenizer.ggml.add_bos_token": false,
      "tokenizer.ggml.bos_token_id": 151643,
      "tokenizer.ggml.eos_token_id": 151645,
      "tokenizer.ggml.merges": null,
      "tokenizer.ggml.model": "gpt2",
      "tokenizer.ggml.padding_token_id": 151643,
      "tokenizer.ggml.pre": "qwen2",
      "tokenizer.ggml.token_type": null,
      "tokenizer.ggml.tokens": null
    }
  }
}
```

## Chat

- status: `200`
- elapsed_seconds: `7.208`
- answer: Приватный AI-сервис - это сервис, который предоставляет доступ к интеллектуальному программированию и машинному обучению для пользователей, не имеющих доступа к более высоким технологиям.

## Stability

| request | status | ok | elapsed s |
|---:|---:|---|---:|
| 1 | 200 | True | 1.897 |
| 2 | 200 | True | 2.072 |
| 3 | 200 | True | 2.026 |
| 4 | 200 | True | 2.148 |
| 5 | 200 | True | 2.025 |

## Rate Limit

| request | status | elapsed s |
|---:|---:|---:|
| 1 | 200 | 2.178 |
| 2 | 200 | 1.899 |
| 3 | 429 | 0.24 |
| 4 | 429 | 0.228 |
| 5 | 429 | 0.239 |
| 6 | 429 | 0.248 |
| 7 | 429 | 0.256 |
| 8 | 429 | 0.242 |
| 9 | 429 | 0.251 |
| 10 | 429 | 0.242 |
| 11 | 429 | 0.224 |
| 12 | 429 | 0.237 |

## Max Context

```json
{
  "messages_before": 44,
  "messages_sent": 5,
  "input_chars_before": 170616,
  "input_chars_sent": 8193,
  "max_messages": 12,
  "max_input_chars": 12000,
  "truncated": true
}
```

## Raw Ollama Exposure

```json
{
  "raw_ollama_url": "http://138.16.168.37:11434/api/tags",
  "open": false,
  "error": "Remote end closed connection without response",
  "elapsed_seconds": 1.398
}
```
