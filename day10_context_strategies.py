import json
import math
import os
import re
from copy import deepcopy
from pathlib import Path


STRATEGIES = {"sliding", "facts", "branching"}
FACT_PREFIXES = {
    "цель": "goal",
    "ограничение": "constraint",
    "предпочтение": "preference",
    "решение": "decision",
    "договоренность": "agreement",
    "факт": "fact",
    "проект": "project",
    "дедлайн": "deadline",
    "бюджет": "budget",
    "стек": "stack",
}


class SimpleTokenCounter:
    def count_text(self, text: str) -> int:
        if not text:
            return 0
        ascii_chars = 0
        non_ascii_chars = 0
        for char in text:
            if char.isspace():
                continue
            if ord(char) < 128:
                ascii_chars += 1
            else:
                non_ascii_chars += 1
        return max(1, math.ceil(ascii_chars * 0.3 + non_ascii_chars * 0.6))

    def count_message(self, message: dict[str, str]) -> int:
        return 4 + self.count_text(message.get("role", "")) + self.count_text(message.get("content", ""))

    def count_messages(self, messages: list[dict[str, str]]) -> int:
        return sum(self.count_message(message) for message in messages)


def default_chat() -> dict[str, object]:
    return {
        "enabled": False,
        "strategy": "sliding",
        "sliding_messages": [],
        "facts": {},
        "facts_messages": [],
        "branches": {"main": {"messages": [], "facts": {}}},
        "active_branch": "main",
        "checkpoints": {},
    }


def sanitize_messages(raw_messages: object) -> list[dict[str, str]]:
    if not isinstance(raw_messages, list):
        return []
    messages: list[dict[str, str]] = []
    for raw_message in raw_messages:
        if not isinstance(raw_message, dict):
            continue
        role = raw_message.get("role")
        content = raw_message.get("content")
        if role in {"user", "assistant", "system"} and isinstance(content, str):
            messages.append({"role": role, "content": content})
    return messages


def extract_facts(text: str) -> dict[str, str]:
    facts: dict[str, str] = {}
    for raw_line in re.split(r"[\n;]+", text):
        line = raw_line.strip()
        if not line:
            continue
        if ":" in line:
            key, value = line.split(":", 1)
            normalized = FACT_PREFIXES.get(key.strip().lower())
            if normalized and value.strip():
                facts[normalized] = value.strip()
        name_match = re.search(r"(?:меня зовут|пользователя зовут)\s+([А-Яа-яA-Za-z0-9_-]+)", line, re.IGNORECASE)
        if name_match:
            facts["user_name"] = name_match.group(1)
        project_match = re.search(r"проект (?:называется|название)\s+([А-Яа-яA-Za-z0-9_-]+)", line, re.IGNORECASE)
        if project_match:
            facts["project"] = project_match.group(1)
    return facts


def facts_to_message(facts: dict[str, str]) -> dict[str, str] | None:
    if not facts:
        return None
    lines = [f"{key}: {value}" for key, value in sorted(facts.items())]
    return {"role": "system", "content": "Sticky facts / key-value memory:\n" + "\n".join(lines)}


class Day10MemoryStore:
    def __init__(self, path: Path, recent_messages: int) -> None:
        self.path = path
        self.recent_messages = recent_messages
        self._chats = self._load()

    def is_enabled(self, chat_id: int) -> bool:
        return bool(self._chat(chat_id).get("enabled", False))

    def enable(self, chat_id: int, strategy: str) -> None:
        if strategy not in STRATEGIES:
            raise ValueError(f"Unknown strategy: {strategy}. Use sliding, facts, branching.")
        chat = self._chat(chat_id)
        chat["enabled"] = True
        chat["strategy"] = strategy
        self._save()

    def disable(self, chat_id: int) -> None:
        self._chat(chat_id)["enabled"] = False
        self._save()

    def strategy(self, chat_id: int) -> str:
        return str(self._chat(chat_id).get("strategy", "sliding"))

    def build_history(self, chat_id: int) -> list[dict[str, str]]:
        chat = self._chat(chat_id)
        strategy = str(chat.get("strategy", "sliding"))
        if strategy == "sliding":
            return list(chat["sliding_messages"])[-self.recent_messages :]
        if strategy == "facts":
            messages = []
            facts_message = facts_to_message(chat["facts"])
            if facts_message:
                messages.append(facts_message)
            messages.extend(list(chat["facts_messages"])[-self.recent_messages :])
            return messages
        if strategy == "branching":
            branch = self._active_branch(chat)
            messages = []
            facts_message = facts_to_message(branch.get("facts", {}))
            if facts_message:
                messages.append(facts_message)
            messages.extend(list(branch["messages"])[-self.recent_messages :])
            return messages
        return []

    def append_exchange(self, chat_id: int, user_request: str, answer: str) -> None:
        chat = self._chat(chat_id)
        strategy = str(chat.get("strategy", "sliding"))
        exchange = [{"role": "user", "content": user_request}, {"role": "assistant", "content": answer}]
        if strategy == "sliding":
            chat["sliding_messages"].extend(exchange)
            del chat["sliding_messages"][:-self.recent_messages]
        elif strategy == "facts":
            chat["facts"].update(extract_facts(user_request))
            chat["facts_messages"].extend(exchange)
            del chat["facts_messages"][:-self.recent_messages]
        elif strategy == "branching":
            branch = self._active_branch(chat)
            branch["facts"].update(extract_facts(user_request))
            branch["messages"].extend(exchange)
            del branch["messages"][:-self.recent_messages]
        self._save()

    def create_checkpoint(self, chat_id: int, name: str) -> None:
        chat = self._chat(chat_id)
        chat["checkpoints"][name] = deepcopy(self._active_branch(chat))
        self._save()

    def create_branch(self, chat_id: int, name: str, checkpoint_name: str | None = None) -> None:
        chat = self._chat(chat_id)
        if checkpoint_name:
            if checkpoint_name not in chat["checkpoints"]:
                raise ValueError(f"Checkpoint not found: {checkpoint_name}")
            source = chat["checkpoints"][checkpoint_name]
        else:
            source = self._active_branch(chat)
        chat["branches"][name] = deepcopy(source)
        chat["active_branch"] = name
        chat["strategy"] = "branching"
        chat["enabled"] = True
        self._save()

    def switch_branch(self, chat_id: int, name: str) -> None:
        chat = self._chat(chat_id)
        if name not in chat["branches"]:
            raise ValueError(f"Branch not found: {name}")
        chat["active_branch"] = name
        chat["strategy"] = "branching"
        chat["enabled"] = True
        self._save()

    def status(self, chat_id: int) -> str:
        chat = self._chat(chat_id)
        strategy = self.strategy(chat_id)
        active_branch = chat.get("active_branch", "main")
        facts = chat.get("facts", {})
        if strategy == "branching":
            facts = self._active_branch(chat).get("facts", {})
        history = self.build_history(chat_id)
        tokens = SimpleTokenCounter().count_messages(history)
        return (
            f"Day10 context status\n"
            f"enabled={chat.get('enabled', False)}\n"
            f"strategy={strategy}\n"
            f"active_branch={active_branch}\n"
            f"branches={', '.join(sorted(chat['branches'].keys()))}\n"
            f"checkpoints={', '.join(sorted(chat['checkpoints'].keys())) or '-'}\n"
            f"facts={len(facts)}\n"
            f"context_messages={len(history)}\n"
            f"context_tokens~{tokens}"
        )

    def _active_branch(self, chat: dict[str, object]) -> dict[str, object]:
        active_branch = str(chat.get("active_branch", "main"))
        branches = chat["branches"]
        if active_branch not in branches:
            branches[active_branch] = {"messages": [], "facts": {}}
        return branches[active_branch]

    def _chat(self, chat_id: int) -> dict[str, object]:
        return self._chats.setdefault(str(chat_id), default_chat())

    def _load(self) -> dict[str, dict[str, object]]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        chats = raw.get("chats", raw) if isinstance(raw, dict) else {}
        if not isinstance(chats, dict):
            return {}
        normalized: dict[str, dict[str, object]] = {}
        for chat_id, raw_chat in chats.items():
            chat = default_chat()
            if isinstance(raw_chat, dict):
                chat.update(raw_chat)
            chat["sliding_messages"] = sanitize_messages(chat.get("sliding_messages", []))
            chat["facts_messages"] = sanitize_messages(chat.get("facts_messages", []))
            if not isinstance(chat.get("facts"), dict):
                chat["facts"] = {}
            if not isinstance(chat.get("branches"), dict):
                chat["branches"] = {"main": {"messages": [], "facts": {}}}
            for branch_name, branch in list(chat["branches"].items()):
                if not isinstance(branch, dict):
                    chat["branches"][branch_name] = {"messages": [], "facts": {}}
                    continue
                branch["messages"] = sanitize_messages(branch.get("messages", []))
                if not isinstance(branch.get("facts"), dict):
                    branch["facts"] = {}
            if not isinstance(chat.get("checkpoints"), dict):
                chat["checkpoints"] = {}
            normalized[str(chat_id)] = chat
        return normalized

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_name(f"{self.path.name}.tmp")
        temp_path.write_text(json.dumps({"chats": self._chats}, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp_path, self.path)


def build_requirements_scenario() -> list[str]:
    return [
        "цель: собрать ТЗ для Telegram-агента поддержки",
        "ограничение: ответ должен быть до 2 секунд",
        "предпочтение: хранить важные решения между запусками",
        "решение: используем DeepSeek API",
        "договоренность: интерфейс через Telegram команды",
        "стек: Python, python-telegram-bot, JSON",
        "бюджет: минимальный, без базы данных",
        "дедлайн: пятница",
        "Шум: обсуждаем тексты кнопок и формат приветствия",
        "Шум: обсуждаем цвета, которых в Telegram-боте нет",
        "Шум: спорим про название раздела помощи",
        "Шум: повторяем, что нужно не забыть финальный отчет",
        "Финальный вопрос: перечисли цель, ограничения, решения, стек, бюджет и дедлайн.",
    ]


def evaluate_strategy(name: str, scenario: list[str], recent_messages: int = 6) -> dict[str, object]:
    counter = SimpleTokenCounter()
    facts: dict[str, str] = {}
    messages: list[dict[str, str]] = []
    branches: dict[str, list[dict[str, str]]] = {"cheap": [], "quality": []}
    active_branch = "cheap"

    if name == "branching":
        base = scenario[:8]
        for item in base:
            branches["cheap"].append({"role": "user", "content": item})
            branches["cheap"].append({"role": "assistant", "content": "Запомнил."})
            branches["quality"].append({"role": "user", "content": item})
            branches["quality"].append({"role": "assistant", "content": "Запомнил."})
        branches["cheap"].append({"role": "user", "content": "решение: экономим токены, минимум деталей"})
        branches["quality"].append({"role": "user", "content": "решение: сохраняем максимум деталей для стабильности"})
        active_branch = "quality"
        context = branches[active_branch][-recent_messages:]
        text = "\n".join(message["content"] for message in context)
    else:
        for item in scenario[:-1]:
            if name == "facts":
                facts.update(extract_facts(item))
            messages.append({"role": "user", "content": item})
            messages.append({"role": "assistant", "content": "Запомнил."})
        if name == "sliding":
            context = messages[-recent_messages:]
        else:
            facts_message = facts_to_message(facts)
            context = ([facts_message] if facts_message else []) + messages[-recent_messages:]
        text = "\n".join(message["content"] for message in context)

    expected = ["goal", "constraint", "decision", "stack", "budget", "deadline"]
    labels = {
        "goal": "Telegram-агента поддержки",
        "constraint": "2 секунд",
        "decision": "DeepSeek API",
        "stack": "Python",
        "budget": "минимальный",
        "deadline": "пятница",
    }
    quality = sum(1 for needle in labels.values() if needle in text)
    tokens = counter.count_messages(context)
    if name == "sliding":
        convenience = "простая, но забывает ранние детали"
        stability = "низкая"
    elif name == "facts":
        convenience = "нужно извлекать факты, зато стабильнее"
        stability = "высокая"
    else:
        convenience = "удобно сравнивать варианты, нужен switch branch"
        stability = "средняя: ветки независимы"
    return {
        "strategy": name,
        "quality": quality,
        "expected": len(expected),
        "tokens": tokens,
        "stability": stability,
        "convenience": convenience,
        "answer": "; ".join(needle for needle in labels.values() if needle in text) or "детали потеряны",
    }


def build_day10_report() -> str:
    scenario = build_requirements_scenario()
    results = [evaluate_strategy(name, scenario) for name in ["sliding", "facts", "branching"]]
    lines = [
        "День 10. Стратегии контекста без summary",
        "",
        "Сценарий: собираем ТЗ 13 сообщений.",
        "Команды: /strategy sliding|facts|branching, /checkpoint NAME, /branch NAME [CHECKPOINT], /switch_branch NAME.",
        "",
    ]
    for result in results:
        lines.extend(
            [
                result["strategy"],
                f"quality={result['quality']}/{result['expected']}",
                f"tokens~{result['tokens']}",
                f"stability={result['stability']}",
                f"answer={result['answer']}",
                f"UX={result['convenience']}",
                "",
            ]
        )
    lines.append("Вывод: sliding дешевле, но теряет детали; facts стабильнее; branching нужен для независимых вариантов.")
    return "\n".join(lines)


def main() -> None:
    print(build_day10_report())


if __name__ == "__main__":
    main()
