import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from bot import (
    DEFAULT_MODEL,
    AgentResponse,
    JsonHistoryStore,
    TokenCounter,
    TokenStats,
    estimate_request_cost_usd,
    parse_positive_int,
)


@dataclass(frozen=True)
class ExperimentResult:
    name: str
    turns: int
    history_tokens: int
    prompt_tokens: int
    response_tokens: int
    total_tokens: int
    context_limit: int
    remaining_tokens: int
    cost_usd: float
    status: str


class SimulatedAgent:
    def __init__(self, model: str, context_limit: int, max_output_tokens: int) -> None:
        self.model = model
        self.context_limit = context_limit
        self.max_output_tokens = max_output_tokens
        self.token_counter = TokenCounter()
        self.system_message = {
            "role": "system",
            "content": "You are SimpleDeepSeekAgent. Answer in Russian and use previous messages as context.",
        }

    def handle(self, user_request: str, history: list[dict[str, str]]) -> AgentResponse:
        messages = [self.system_message, *history, {"role": "user", "content": user_request}]
        current_request_tokens = self.token_counter.count_text(user_request)
        history_tokens = self.token_counter.count_messages(history)
        prompt_tokens = self.token_counter.count_messages(messages)

        if prompt_tokens + self.max_output_tokens > self.context_limit:
            raise ValueError(
                f"Контекст переполнен: prompt ~{prompt_tokens} + max_output {self.max_output_tokens} "
                f"> limit {self.context_limit}."
            )

        answer = f"Ответ на запрос: {user_request[:80]}"
        response_tokens = self.token_counter.count_text(answer)
        cost_usd = estimate_request_cost_usd(
            model=self.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=response_tokens,
            cache_hit_tokens=0,
            cache_miss_tokens=prompt_tokens,
        )
        return AgentResponse(
            user_request=user_request,
            answer=answer,
            model=self.model,
            history_messages=len(messages),
            token_stats=TokenStats(
                current_request_tokens=current_request_tokens,
                history_tokens=history_tokens,
                prompt_tokens_estimate=prompt_tokens,
                response_tokens_estimate=response_tokens,
                prompt_tokens_actual=prompt_tokens,
                response_tokens_actual=response_tokens,
                total_tokens_actual=prompt_tokens + response_tokens,
                context_limit=self.context_limit,
                context_remaining_estimate=self.context_limit - prompt_tokens - response_tokens,
                cost_usd_estimate=cost_usd,
            ),
        )


def build_short_dialog() -> list[str]:
    return [
        "Привет. Меня зовут Анна.",
        "Запомни, что мой проект называется AiTgsterBot.",
        "Как называется мой проект?",
    ]


def build_long_dialog() -> list[str]:
    base = (
        "Сохрани деталь проекта: Telegram-агент использует DeepSeek API, JSON-память, "
        "подсчет токенов, стоимость запроса и защиту от переполнения контекста. "
    )
    return [f"Шаг {index}. {base * 8}" for index in range(1, 31)]


def build_overflow_dialog() -> list[str]:
    long_fact = (
        "Переполнение контекста наступает, когда сумма токенов system, истории, текущего запроса "
        "и зарезервированного ответа превышает лимит модели. "
    )
    return [f"Большой блок {index}. {long_fact * 20}" for index in range(1, 20)]


def run_dialog(
    name: str,
    prompts: list[str],
    context_limit: int,
    max_output_tokens: int,
    history_file: Path,
) -> ExperimentResult:
    if history_file.exists():
        history_file.unlink()

    store = JsonHistoryStore(path=history_file, max_messages=10_000)
    agent = SimulatedAgent(model=DEFAULT_MODEL, context_limit=context_limit, max_output_tokens=max_output_tokens)
    last_response: AgentResponse | None = None

    try:
        for prompt in prompts:
            history = store.get_history(chat_id=1)
            last_response = agent.handle(prompt, history)
            store.append_exchange(1, prompt, last_response.answer)
    except ValueError as error:
        history_tokens = agent.token_counter.count_messages(store.get_history(chat_id=1))
        return ExperimentResult(
            name=name,
            turns=len(store.get_history(chat_id=1)) // 2,
            history_tokens=history_tokens,
            prompt_tokens=0,
            response_tokens=0,
            total_tokens=0,
            context_limit=context_limit,
            remaining_tokens=context_limit - history_tokens - max_output_tokens,
            cost_usd=0.0,
            status=str(error),
        )

    if last_response is None:
        raise RuntimeError("Dialog has no prompts")

    stats = last_response.token_stats
    return ExperimentResult(
        name=name,
        turns=len(prompts),
        history_tokens=stats.history_tokens,
        prompt_tokens=stats.prompt_tokens_actual,
        response_tokens=stats.response_tokens_actual,
        total_tokens=stats.total_tokens_actual,
        context_limit=stats.context_limit,
        remaining_tokens=stats.context_remaining_estimate,
        cost_usd=stats.cost_usd_estimate,
        status="ok",
    )


def print_result(result: ExperimentResult) -> None:
    print(f"\n{result.name}")
    print(f"turns: {result.turns}")
    print(f"history_tokens: ~{result.history_tokens}")
    print(f"prompt_tokens: ~{result.prompt_tokens}")
    print(f"response_tokens: ~{result.response_tokens}")
    print(f"total_tokens: ~{result.total_tokens}")
    print(f"context_limit: {result.context_limit}")
    print(f"remaining_tokens: ~{result.remaining_tokens}")
    print(f"cost_usd: ~${result.cost_usd:.6f}")
    print(f"status: {result.status}")


def main() -> None:
    load_dotenv()
    max_output_tokens = parse_positive_int(os.getenv("MAX_OUTPUT_TOKENS"), 256)
    work_dir = Path(os.getenv("TOKEN_EXPERIMENT_DIR", ".token_experiment"))
    work_dir.mkdir(parents=True, exist_ok=True)

    results = [
        run_dialog(
            name="Короткий диалог",
            prompts=build_short_dialog(),
            context_limit=20_000,
            max_output_tokens=max_output_tokens,
            history_file=work_dir / "short.json",
        ),
        run_dialog(
            name="Длинный диалог",
            prompts=build_long_dialog(),
            context_limit=20_000,
            max_output_tokens=max_output_tokens,
            history_file=work_dir / "long.json",
        ),
        run_dialog(
            name="Переполнение контекста",
            prompts=build_overflow_dialog(),
            context_limit=3_000,
            max_output_tokens=max_output_tokens,
            history_file=work_dir / "overflow.json",
        ),
    ]

    print("Day 8 token experiment")
    for result in results:
        print_result(result)

    print("\nСравнение")
    for result in results:
        print(
            f"- {result.name}: history ~{result.history_tokens}, total ~{result.total_tokens}, "
            f"cost ~${result.cost_usd:.6f}, status {result.status}"
        )


if __name__ == "__main__":
    main()
