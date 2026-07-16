from dataclasses import dataclass

from bot import DEFAULT_MODEL, TokenCounter, estimate_request_cost_usd


@dataclass(frozen=True)
class DemoTurn:
    number: int
    request_tokens: int
    history_tokens: int
    prompt_tokens: int
    response_tokens: int
    total_tokens: int
    remaining_tokens: int
    cost_usd: float


@dataclass(frozen=True)
class DemoScenario:
    name: str
    context_limit: int
    max_output_tokens: int
    turns: list[DemoTurn]
    status: str
    failed_turn: int | None = None


def short_dialog() -> list[str]:
    return [
        "Привет. Меня зовут Анна.",
        "Запомни, что мой проект называется AiTgsterBot.",
        "Как называется мой проект?",
    ]


def long_dialog() -> list[str]:
    base = (
        "Сохрани деталь проекта: Telegram-агент использует DeepSeek API, JSON-память, "
        "подсчет токенов, стоимость запроса и защиту от переполнения контекста. "
    )
    return [f"Шаг {index}. {base * 8}" for index in range(1, 31)]


def overflow_dialog() -> list[str]:
    long_fact = (
        "Переполнение контекста наступает, когда сумма токенов system, истории, текущего запроса "
        "и зарезервированного ответа превышает лимит модели. "
    )
    return [f"Большой блок {index}. {long_fact * 20}" for index in range(1, 20)]


def run_demo_dialog(
    name: str,
    prompts: list[str],
    context_limit: int,
    max_output_tokens: int,
) -> DemoScenario:
    counter = TokenCounter()
    history: list[dict[str, str]] = []
    turns: list[DemoTurn] = []
    system_message = {
        "role": "system",
        "content": "You are SimpleDeepSeekAgent. Answer in Russian and use previous messages as context.",
    }

    for index, prompt in enumerate(prompts, start=1):
        messages = [system_message, *history, {"role": "user", "content": prompt}]
        request_tokens = counter.count_text(prompt)
        history_tokens = counter.count_messages(history)
        prompt_tokens = counter.count_messages(messages)

        if prompt_tokens + max_output_tokens > context_limit:
            return DemoScenario(
                name=name,
                context_limit=context_limit,
                max_output_tokens=max_output_tokens,
                turns=turns,
                failed_turn=index,
                status=(
                    "Контекст переполнен: "
                    f"prompt ~{prompt_tokens} + max_output {max_output_tokens} > limit {context_limit}. "
                    "Вызов модели не выполняется, ответа нет, история дальше не растет."
                ),
            )

        answer = f"Демо-ответ на ход {index}: учел контекст и продолжаю диалог."
        response_tokens = counter.count_text(answer)
        cost_usd = estimate_request_cost_usd(
            model=DEFAULT_MODEL,
            prompt_tokens=prompt_tokens,
            completion_tokens=response_tokens,
            cache_hit_tokens=0,
            cache_miss_tokens=prompt_tokens,
        )
        turns.append(
            DemoTurn(
                number=index,
                request_tokens=request_tokens,
                history_tokens=history_tokens,
                prompt_tokens=prompt_tokens,
                response_tokens=response_tokens,
                total_tokens=prompt_tokens + response_tokens,
                remaining_tokens=context_limit - prompt_tokens - response_tokens,
                cost_usd=cost_usd,
            )
        )
        history.extend(
            [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": answer},
            ]
        )

    return DemoScenario(
        name=name,
        context_limit=context_limit,
        max_output_tokens=max_output_tokens,
        turns=turns,
        status="ok",
    )


def build_day8_scenarios(max_output_tokens: int = 256) -> list[DemoScenario]:
    return [
        run_demo_dialog(
            name="Короткий диалог",
            prompts=short_dialog(),
            context_limit=20_000,
            max_output_tokens=max_output_tokens,
        ),
        run_demo_dialog(
            name="Длинный диалог",
            prompts=long_dialog(),
            context_limit=20_000,
            max_output_tokens=max_output_tokens,
        ),
        run_demo_dialog(
            name="Диалог выше лимита модели",
            prompts=overflow_dialog(),
            context_limit=3_000,
            max_output_tokens=max_output_tokens,
        ),
    ]


def selected_turns(turns: list[DemoTurn]) -> list[DemoTurn]:
    if len(turns) <= 8:
        return turns
    wanted = {1, 2, 3, 5, 10, 20, len(turns)}
    return [turn for turn in turns if turn.number in wanted]


def format_scenario(scenario: DemoScenario) -> str:
    lines = [
        f"{scenario.name}",
        f"limit={scenario.context_limit}, max_output={scenario.max_output_tokens}",
        "ход | запрос | история | prompt | ответ | total | цена | остаток",
    ]
    for turn in selected_turns(scenario.turns):
        lines.append(
            f"{turn.number} | ~{turn.request_tokens} | ~{turn.history_tokens} | "
            f"~{turn.prompt_tokens} | ~{turn.response_tokens} | ~{turn.total_tokens} | "
            f"${turn.cost_usd:.6f} | ~{turn.remaining_tokens}"
        )

    if scenario.turns:
        first = scenario.turns[0]
        last = scenario.turns[-1]
        cost_growth = last.cost_usd / first.cost_usd if first.cost_usd else 0
        token_growth = last.total_tokens / first.total_tokens if first.total_tokens else 0
        lines.append(
            f"рост: total x{token_growth:.1f}, стоимость x{cost_growth:.1f} "
            f"({first.total_tokens} -> {last.total_tokens} токенов)"
        )

    if scenario.failed_turn is not None:
        lines.append(f"сломалось на ходе {scenario.failed_turn}: {scenario.status}")
    else:
        lines.append(f"статус: {scenario.status}")

    return "\n".join(lines)


def build_day8_chat_report(max_output_tokens: int = 256) -> str:
    scenarios = build_day8_scenarios(max_output_tokens=max_output_tokens)
    lines = [
        "День 8. Работа с токенами",
        "",
        "Это вывод прямо для чата: короткий диалог, длинный диалог и реальное переполнение заданного context_limit.",
        "Счетчик локальный: до вызова модели показывает оценку, после реального API бот еще показывает usage от DeepSeek.",
        "",
    ]
    lines.extend(format_scenario(scenario) + "\n" for scenario in scenarios)
    lines.append(
        "Что ломается при переполнении: запрос не отправляется в модель, потому что prompt + max_output уже больше лимита. "
        "Это защищает от ошибки API и лишних затрат."
    )
    return "\n".join(lines)


def main() -> None:
    print(build_day8_chat_report())


if __name__ == "__main__":
    main()
