# Day 8 Token Report

Telegram commands:

```text
/day8
/tokens
/day8_api
```

`/day8` and `/tokens` show local token-growth demo in chat.

`/day8_api` performs real DeepSeek API calls:

1. short API request;
2. long API request that almost fills the model context window;
3. third API request that exceeds the model context because the long second request is already in history.

## Local Chat Report

Command:

```powershell
.venv\Scripts\python.exe day8_demo.py
```

Output was sent to Telegram:

```text
sent_day8_report_to_chat:439057315
```

## Real API Overflow

Command:

```powershell
.venv\Scripts\python.exe day8_real_api_overflow.py --send-telegram --second-repeated-tokens 1000000 --third-repeated-tokens 80000
```

Output:

```text
Day 8. Реальный API overflow
model=deepseek-v4-flash
second_request_repeated_tokens=1000000
third_request_repeated_tokens=80000
max_tokens=1 для всех трех вызовов, чтобы почти убрать output-cost.
Сценарий: 1-й короткий, 2-й успешно почти заполняет историю, 3-й падает из-за накопленного контекста.

1. Короткий API-запрос: OK за 1.5s
prompt=40, answer=1, total=41
cache_hit=0, cache_miss=40
cost~$0.000006
answer='За'

2. Длинный API-запрос: OK за 22.8s
prompt=1000055, answer=1, total=1000056
cache_hit=0, cache_miss=1000055
cost~$0.140008
answer='П'

3. API-запрос выше лимита модели: ERROR за 4.9s
prompt=0, answer=0, total=0, cost~$0.000000
BadRequestError: status=400; body={"error":{"message":"This model's maximum context length is 1048565 tokens. However, you requested 1080073 tokens (1080072 in the messages, 1 in the completion). Please reduce the length of the messages or completion.","type":"invalid_request_error","param":null,"code":"invalid_request_error"}}

Вывод: третий запрос действительно дошел до DeepSeek API и сломался на лимите модели. После ошибки ответа модели нет, usage не возвращается, диалог продолжать этим запросом нельзя.
sent_real_api_overflow_report_to_chat:439057315
```

Result:

- second request filled the context window with `1,000,055` prompt tokens;
- third request added `80,000` repeated prompt tokens;
- API rejected the third request because requested context was `1,080,073` tokens while model maximum was `1,048,565`;
- no model answer and no usage came back for the failed third request.
