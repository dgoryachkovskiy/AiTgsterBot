from dataclasses import dataclass

from bot import DEFAULT_RECENT_MESSAGES, DEFAULT_SUMMARY_BATCH_SIZE, TokenCounter


FACTS = {
    "name": "Данил",
    "project": "AiTgsterBot",
    "deadline": "пятница",
}


@dataclass(frozen=True)
class DemoResult:
    mode: str
    context_tokens: int
    answer: str
    quality_score: int
    details: str


def build_dialog() -> list[dict[str, str]]:
    messages = [
        {"role": "user", "content": "FACT: пользователя зовут Данил."},
        {"role": "assistant", "content": "Запомнил имя пользователя: Данил."},
        {"role": "user", "content": "FACT: проект называется AiTgsterBot."},
        {"role": "assistant", "content": "Запомнил проект: AiTgsterBot."},
        {"role": "user", "content": "FACT: дедлайн демонстрации в пятницу."},
        {"role": "assistant", "content": "Запомнил дедлайн: пятница."},
    ]
    for index in range(1, 31):
        messages.extend(
            [
                {
                    "role": "user",
                    "content": (
                        f"Шум {index}: обсуждаем Telegram polling, JSON, токены, стоимость, "
                        "ошибки API, summary и формат отчета."
                    ),
                },
                {"role": "assistant", "content": f"Принял рабочую деталь {index}."},
            ]
        )
    return messages


def make_summary(old_summary: str, messages: list[dict[str, str]]) -> str:
    facts = [line.strip() for line in old_summary.splitlines() if line.strip()]
    for message in messages:
        content = message["content"]
        if content.startswith("FACT:"):
            facts.append(content.replace("FACT:", "SUMMARY:", 1).strip())
    unique = []
    seen: set[str] = set()
    for fact in facts:
        if fact not in seen:
            seen.add(fact)
            unique.append(fact)
    return "\n".join(unique)


def compress_history(
    messages: list[dict[str, str]],
    recent_messages: int = DEFAULT_RECENT_MESSAGES,
    batch_size: int = DEFAULT_SUMMARY_BATCH_SIZE,
) -> tuple[str, list[dict[str, str]], int]:
    summary = ""
    remaining = list(messages)
    compressed = 0
    while len(remaining) > recent_messages:
        old_count = len(remaining) - recent_messages
        batch = remaining[: min(batch_size, old_count)]
        summary = make_summary(summary, batch)
        remaining = remaining[len(batch) :]
        compressed += len(batch)
    return summary, remaining, compressed


def messages_text(messages: list[dict[str, str]]) -> str:
    return "\n".join(f"{message['role']}: {message['content']}" for message in messages)


def answer_from_context(context_text: str) -> str:
    parts = []
    if FACTS["name"] in context_text:
        parts.append(f"имя: {FACTS['name']}")
    if FACTS["project"] in context_text:
        parts.append(f"проект: {FACTS['project']}")
    if "пятниц" in context_text:
        parts.append(f"дедлайн: {FACTS['deadline']}")
    return "; ".join(parts) if parts else "не знаю: старые факты не попали в контекст"


def quality_score(answer: str) -> int:
    return sum(1 for value in FACTS.values() if value in answer)


def evaluate_modes() -> list[DemoResult]:
    counter = TokenCounter()
    dialog = build_dialog()
    question = {"role": "user", "content": "Как меня зовут, какой проект и когда дедлайн?"}

    full_context = [*dialog, question]
    full_text = messages_text(full_context)
    full_answer = answer_from_context(full_text)

    recent_context = [*dialog[-DEFAULT_RECENT_MESSAGES:], question]
    recent_text = messages_text(recent_context)
    recent_answer = answer_from_context(recent_text)

    summary, recent_messages, compressed = compress_history(dialog)
    compressed_context = [
        {"role": "system", "content": "Summary of earlier dialog:\n" + summary},
        *recent_messages,
        question,
    ]
    compressed_text = messages_text(compressed_context)
    compressed_answer = answer_from_context(compressed_text)

    return [
        DemoResult(
            mode="Без сжатия: полная история",
            context_tokens=counter.count_messages(full_context),
            answer=full_answer,
            quality_score=quality_score(full_answer),
            details=f"messages={len(full_context)}, summary=0",
        ),
        DemoResult(
            mode="Без summary: только последние N",
            context_tokens=counter.count_messages(recent_context),
            answer=recent_answer,
            quality_score=quality_score(recent_answer),
            details=f"messages={len(recent_context)}, dropped={len(dialog) - DEFAULT_RECENT_MESSAGES}",
        ),
        DemoResult(
            mode="Со сжатием: summary + последние N",
            context_tokens=counter.count_messages(compressed_context),
            answer=compressed_answer,
            quality_score=quality_score(compressed_answer),
            details=f"summary_chars={len(summary)}, recent={len(recent_messages)}, compressed={compressed}",
        ),
    ]


def build_day9_report() -> str:
    results = evaluate_modes()
    full_tokens = results[0].context_tokens
    compressed_tokens = results[2].context_tokens
    savings = 100 - (compressed_tokens / full_tokens * 100)
    lines = [
        "День 9. Сжатие истории",
        "",
        f"Настройки: последние N={DEFAULT_RECENT_MESSAGES}, batch={DEFAULT_SUMMARY_BATCH_SIZE}",
        "Вопрос: Как меня зовут, какой проект и когда дедлайн?",
        "",
    ]
    for result in results:
        lines.extend(
            [
                result.mode,
                f"tokens~{result.context_tokens}",
                f"quality={result.quality_score}/3",
                f"answer={result.answer}",
                result.details,
                "",
            ]
        )
    lines.append(f"Экономия summary+recent против full history: {savings:.1f}% токенов.")
    lines.append("Вывод: summary сохраняет старые факты дешевле полной истории; last-N без summary теряет качество.")
    return "\n".join(lines)


def main() -> None:
    print(build_day9_report())


if __name__ == "__main__":
    main()
