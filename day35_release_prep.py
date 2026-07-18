from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError, OpenAI

from day21_document_indexer import read_text


DEFAULT_STORE_DIR = "day35_release_store"
DEFAULT_REPORT_PATH = "DAY35_REAL_TASK_REPORT.md"
DEFAULT_DOC_PATH = "docs/DAY35_RELEASE_PREP.md"
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_TIMEOUT_SECONDS = 90
DEFAULT_VIDEO_OUTPUT = r"C:\Users\pospi\Desktop\курс\7 неделя\day35_real_task.mp4"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
THINKING_DISABLED = {"thinking": {"type": "disabled"}}
MAX_DIFF_CHARS = 90000
MAX_FILE_CHARS = 18000


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


@dataclass(frozen=True)
class ReleaseContext:
    generated_at: str
    project: str
    task: str
    branch: str
    commit: str
    status_short: list[str]
    changed_files: list[str]
    diff_stat: str
    diff: str
    diff_truncated: bool
    file_snapshots: dict[str, str]
    docs: dict[str, str]
    existing_reports: dict[str, str]
    implementation_notes: list[str]
    verification_commands: list[str]


@dataclass(frozen=True)
class ReleaseAnalysis:
    generated_at: str
    model: str
    llm_used: bool
    answer: str
    tokens: dict[str, int]
    elapsed_seconds: float
    error: str = ""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def to_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def parse_int(value: str | None, default: int) -> int:
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def store_dir_from_env(value: str | None = None) -> Path:
    load_dotenv()
    return Path(value or os.getenv("DAY35_RELEASE_STORE_DIR", DEFAULT_STORE_DIR)).resolve()


def video_output_from_env(value: str | None = None) -> Path:
    load_dotenv()
    return Path(value or os.getenv("DAY35_VIDEO_OUTPUT", DEFAULT_VIDEO_OUTPUT)).resolve()


def run_cmd(args: list[str], cwd: Path, check: bool = False) -> str:
    completed = subprocess.run(
        args,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"command failed: {' '.join(args)}")
    return completed.stdout.strip()


def git(root: Path, *args: str) -> str:
    return run_cmd(["git", *args], root, check=True)


def read_project_text(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    text = read_text(path) or ""
    return text[:MAX_FILE_CHARS]


def collect_docs(root: Path) -> dict[str, str]:
    candidates = [
        root / "README.md",
        root / "docs" / "PROJECT_COMMANDS.md",
        root / "docs" / "PROJECT_STRUCTURE.md",
        root / "docs" / "DAY34_FILE_ASSISTANT_USAGE.md",
        root / "DAY34_FILE_ASSISTANT_REPORT.md",
    ]
    docs: dict[str, str] = {}
    for path in candidates:
        if path.exists():
            docs[str(path.relative_to(root)).replace("\\", "/")] = read_project_text(path)
    return docs


def collect_existing_reports(root: Path) -> dict[str, str]:
    reports = {}
    for path in sorted(root.glob("DAY3*_*.md")):
        if path.name in {DEFAULT_REPORT_PATH}:
            continue
        reports[path.name] = read_project_text(path)[:6000]
    return reports


def changed_files_from_status(status_lines: list[str]) -> list[str]:
    files = []
    for line in status_lines:
        if len(line) > 3 and line[:2] in {"??", "!!"}:
            raw = line[3:]
        elif len(line) > 3 and line[2] == " ":
            raw = line[3:]
        elif len(line) > 2:
            raw = line[2:]
        else:
            raw = line
        raw = raw.strip()
        if " -> " in raw:
            raw = raw.split(" -> ", 1)[1].strip()
        if raw and raw not in files:
            files.append(raw)
    return files


def collect_file_snapshots(root: Path, changed_files: list[str]) -> dict[str, str]:
    priority = [
        "day35_release_prep.py",
        "day35_release_video.py",
        "docs/DAY35_RELEASE_PREP.md",
        "DAY35_REAL_TASK_REPORT.md",
        ".env.example",
        ".gitignore",
    ]
    selected = []
    for item in priority + changed_files:
        normalized = item.replace("\\", "/")
        if normalized not in selected:
            selected.append(normalized)

    snapshots: dict[str, str] = {}
    allowed_suffixes = {".py", ".md", ".txt", ".yml", ".yaml", ".json", ".example"}
    for item in selected:
        path = root / item
        if not path.exists() or not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix not in allowed_suffixes and path.name not in {".env.example", ".gitignore"}:
            continue
        snapshots[item] = read_project_text(path)[:12000]
        if len(snapshots) >= 10:
            break
    return snapshots


def collect_release_context(root: Path) -> ReleaseContext:
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    commit = git(root, "rev-parse", "--short", "HEAD")
    status_short = git(root, "status", "--short").splitlines()
    diff_stat = git(root, "diff", "--stat")
    diff_raw = git(root, "diff")
    diff_truncated = len(diff_raw) > MAX_DIFF_CHARS
    diff = diff_raw[:MAX_DIFF_CHARS]
    changed_files = changed_files_from_status(status_short)
    file_snapshots = collect_file_snapshots(root, changed_files)
    return ReleaseContext(
        generated_at=now_iso(),
        project="AiTgsterBot",
        task="подготовка релиза проекта AiTgsterBot",
        branch=branch,
        commit=commit,
        status_short=status_short,
        changed_files=changed_files,
        diff_stat=diff_stat,
        diff=diff,
        diff_truncated=diff_truncated,
        file_snapshots=file_snapshots,
        docs=collect_docs(root),
        existing_reports=collect_existing_reports(root),
        implementation_notes=[
            "day35_release_prep.py is the CLI for collect/analyze/write/package/video.",
            "collect reads git branch/status/diff, changed files, documentation, existing reports, and selected file snapshots.",
            "collect saves day35_release_store/last_collect.json exactly; there is no last_context.json artifact.",
            "analyze sends the collected release context to DeepSeek and saves last_analysis.json.",
            "write --apply writes docs/DAY35_RELEASE_PREP.md, DAY35_REAL_TASK_REPORT.md, and last_run.json.",
            "package writes a JSON manifest with submission files; it does not create a zip archive.",
            "day35_release_video.py creates a terminal-output MP4 using PIL and imageio_ffmpeg; it is not a live screen recording.",
        ],
        verification_commands=[
            ".\\.venv\\Scripts\\python.exe -m py_compile day35_release_prep.py day35_release_video.py",
            ".\\.venv\\Scripts\\python.exe day35_release_prep.py collect",
            ".\\.venv\\Scripts\\python.exe day35_release_prep.py analyze",
            ".\\.venv\\Scripts\\python.exe day35_release_prep.py write --apply",
            ".\\.venv\\Scripts\\python.exe day35_release_prep.py package",
            ".\\.venv\\Scripts\\python.exe day35_release_video.py",
            "git diff --check",
            "ast-index update",
            "git status --short --branch",
        ],
    )


def deepseek_client() -> tuple[OpenAI, str]:
    load_dotenv()
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not set")
    model = os.getenv("DAY35_DEEPSEEK_MODEL") or os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL)
    timeout = parse_int(os.getenv("DAY35_DEEPSEEK_TIMEOUT_SECONDS"), DEFAULT_TIMEOUT_SECONDS)
    return OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL, timeout=timeout, max_retries=0), model


def compact_context(context: ReleaseContext) -> str:
    payload = asdict(context)
    payload["diff"] = context.diff[:65000]
    payload["file_snapshots"] = {key: value[:9000] for key, value in context.file_snapshots.items()}
    payload["docs"] = {key: value[:12000] for key, value in context.docs.items()}
    payload["existing_reports"] = {key: value[:4000] for key, value in context.existing_reports.items()}
    return to_json(payload)


def analyze_context(context: ReleaseContext) -> ReleaseAnalysis:
    started = time.perf_counter()
    try:
        client, model = deepseek_client()
        messages = [
            {
                "role": "system",
                "content": (
                    "Ты release manager для AI-проекта. Отвечай по-русски. "
                    "Сделай реальный release-prep пакет по данным git/status/diff/docs. "
                    "Не выдумывай факты вне контекста. Если детали нет в JSON, напиши, что она не указана. "
                    "Используй implementation_notes и file_snapshots как источник истины по Day35-коду. "
                    "Не называй package zip-архивом, если в контексте сказано, что это JSON manifest. "
                    "Не называй видео live screen recording, если в контексте сказано, что это terminal-output MP4. "
                    "Обязательно укажи, как AI участвует в процессе."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Задача Day35: автоматизировать подготовку релиза AiTgsterBot.\n"
                    "Нужно вернуть Markdown с разделами:\n"
                    "1. Какую задачу решали\n"
                    "2. Что автоматизируется\n"
                    "3. Как AI участвует\n"
                    "4. Release notes\n"
                    "5. Risks / blockers\n"
                    "6. Checklist перед сдачей\n"
                    "7. Команды проверки\n"
                    "8. Файлы для сдачи\n\n"
                    f"Release context JSON:\n{compact_context(context)}"
                ),
            },
        ]
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0,
            max_tokens=2500,
            extra_body=THINKING_DISABLED,
        )
        usage = response.usage
        return ReleaseAnalysis(
            generated_at=now_iso(),
            model=model,
            llm_used=True,
            answer=response.choices[0].message.content or "",
            tokens={
                "prompt": int(getattr(usage, "prompt_tokens", 0) or 0),
                "completion": int(getattr(usage, "completion_tokens", 0) or 0),
                "total": int(getattr(usage, "total_tokens", 0) or 0),
            },
            elapsed_seconds=round(time.perf_counter() - started, 3),
        )
    except (APIConnectionError, APIError, APIStatusError, APITimeoutError, RuntimeError) as exc:
        return ReleaseAnalysis(
            generated_at=now_iso(),
            model=os.getenv("DAY35_DEEPSEEK_MODEL", DEFAULT_MODEL),
            llm_used=False,
            answer=f"DeepSeek analysis failed: {exc}",
            tokens={"prompt": 0, "completion": 0, "total": 0},
            elapsed_seconds=round(time.perf_counter() - started, 3),
            error=str(exc),
        )


def load_context(store_dir: Path) -> ReleaseContext:
    path = store_dir / "last_collect.json"
    if not path.exists():
        raise FileNotFoundError("Run collect first or use write --apply")
    data = json.loads(path.read_text(encoding="utf-8"))
    return ReleaseContext(**data)


def load_analysis(store_dir: Path) -> ReleaseAnalysis:
    path = store_dir / "last_analysis.json"
    if not path.exists():
        raise FileNotFoundError("Run analyze first or use write --apply")
    data = json.loads(path.read_text(encoding="utf-8"))
    return ReleaseAnalysis(**data)


def save_context(store_dir: Path, context: ReleaseContext) -> None:
    store_dir.mkdir(parents=True, exist_ok=True)
    (store_dir / "last_collect.json").write_text(to_json(asdict(context)), encoding="utf-8")


def save_analysis(store_dir: Path, analysis: ReleaseAnalysis) -> None:
    store_dir.mkdir(parents=True, exist_ok=True)
    (store_dir / "last_analysis.json").write_text(to_json(asdict(analysis)), encoding="utf-8")


def render_doc(context: ReleaseContext, analysis: ReleaseAnalysis) -> str:
    return "\n".join(
        [
            "# Day35. AI Release Prep Pipeline",
            "",
            "## Реальная задача",
            "",
            "Решалась задача подготовки релиза проекта AiTgsterBot: собрать изменения, понять риски, подготовить release notes, checklist и пакет для сдачи.",
            "",
            "## Как AI участвует",
            "",
            "- Код собирает реальный контекст репозитория: git branch, status, diff, docs, reports.",
            "- DeepSeek анализирует этот контекст и формирует release summary, risks, checklist и команды проверки.",
            "- Pipeline сохраняет результат в Markdown/JSON и готовит видео-демонстрацию.",
            "",
            "## AI Analysis",
            "",
            analysis.answer.strip(),
            "",
            "## Repo Snapshot",
            "",
            f"- branch: `{context.branch}`",
            f"- commit: `{context.commit}`",
            f"- changed_files: `{len(context.changed_files)}`",
            f"- file_snapshots_read: `{len(context.file_snapshots)}`",
            f"- diff_truncated: `{context.diff_truncated}`",
            f"- llm_used: `{analysis.llm_used}`",
            f"- tokens: `{analysis.tokens}`",
            "",
            "## Changed Files",
            "",
            *[f"- `{item}`" for item in context.changed_files],
            "",
            "## File Snapshots Read",
            "",
            *[f"- `{item}`" for item in context.file_snapshots],
            "",
            "## Verification Commands",
            "",
            "```powershell",
            *context.verification_commands,
            "```",
            "",
        ]
    )


def render_report(context: ReleaseContext, analysis: ReleaseAnalysis, doc_path: Path) -> str:
    return "\n".join(
        [
            "# Day35. Real Task Report",
            "",
            "## Какую задачу решали",
            "",
            "Подготовка релиза проекта AiTgsterBot: собрать изменения, сделать AI-анализ рисков, сформировать release notes, checklist и пакет сдачи.",
            "",
            "## Что автоматизировано",
            "",
            "- Сбор git status/diff/changed files.",
            "- Чтение README/docs/предыдущих отчетов.",
            "- AI-анализ через DeepSeek.",
            "- Создание release документации и JSON trace.",
            "- Подготовка видео-демонстрации.",
            "",
            "## Как AI участвует",
            "",
            f"- DeepSeek model: `{analysis.model}`",
            f"- llm_used: `{analysis.llm_used}`",
            f"- tokens: `{analysis.tokens}`",
            "- AI получает реальный контекст проекта и генерирует release notes, risks, checklist.",
            "",
            "## Реальные артефакты",
            "",
            f"- `{doc_path}`",
            f"- `{DEFAULT_REPORT_PATH}`",
            "- `day35_release_store/last_run.json`",
            "- `day35_release_store/last_collect.json`",
            "- `day35_release_store/last_analysis.json`",
            "",
            "## Реальные файлы прочитаны",
            "",
            *[f"- `{item}`" for item in context.file_snapshots],
            "",
            "## Команды проверки",
            "",
            "```powershell",
            *context.verification_commands,
            "```",
            "",
            "## Сводка AI",
            "",
            analysis.answer.strip(),
            "",
        ]
    )


def command_collect(args: argparse.Namespace) -> int:
    root = Path(args.project_root).resolve()
    store_dir = store_dir_from_env(args.store_dir)
    context = collect_release_context(root)
    save_context(store_dir, context)
    print("day35_collect")
    print(f"project={context.project}")
    print(f"task={context.task}")
    print(f"branch={context.branch}")
    print(f"commit={context.commit}")
    print(f"changed_files={len(context.changed_files)}")
    print(f"docs_read={len(context.docs)}")
    print(f"saved={store_dir / 'last_collect.json'}")
    return 0


def command_analyze(args: argparse.Namespace) -> int:
    root = Path(args.project_root).resolve()
    store_dir = store_dir_from_env(args.store_dir)
    context = collect_release_context(root)
    save_context(store_dir, context)
    analysis = analyze_context(context)
    save_analysis(store_dir, analysis)
    print("day35_analyze")
    print(f"llm_used={analysis.llm_used}")
    print(f"model={analysis.model}")
    print(f"tokens={to_json(analysis.tokens)}")
    if analysis.error:
        print(f"error={analysis.error}")
    preview = analysis.answer
    if len(preview) > 4000:
        preview = preview[:4000] + "\n...truncated; full answer saved to day35_release_store/last_analysis.json..."
    print("analysis_preview:")
    print(preview)
    return 0 if analysis.llm_used else 2


def command_write(args: argparse.Namespace) -> int:
    root = Path(args.project_root).resolve()
    store_dir = store_dir_from_env(args.store_dir)
    context = collect_release_context(root)
    save_context(store_dir, context)
    analysis = analyze_context(context)
    save_analysis(store_dir, analysis)
    doc_path = root / DEFAULT_DOC_PATH
    report_path = root / DEFAULT_REPORT_PATH
    doc = render_doc(context, analysis)
    report = render_report(context, analysis, doc_path)
    run_payload = {
        "context": asdict(context),
        "analysis": asdict(analysis),
        "doc_path": str(doc_path),
        "report_path": str(report_path),
        "video_output": str(video_output_from_env(args.video_output)),
    }
    if args.apply:
        doc_path.parent.mkdir(parents=True, exist_ok=True)
        doc_path.write_text(doc, encoding="utf-8", newline="\n")
        report_path.write_text(report, encoding="utf-8", newline="\n")
        store_dir.mkdir(parents=True, exist_ok=True)
        (store_dir / "last_run.json").write_text(to_json(run_payload), encoding="utf-8")
    print("day35_write")
    print(f"apply={args.apply}")
    print(f"llm_used={analysis.llm_used}")
    print(f"doc_path={doc_path}")
    print(f"report_path={report_path}")
    print(f"last_run={store_dir / 'last_run.json'}")
    return 0 if analysis.llm_used else 2


def command_package(args: argparse.Namespace) -> int:
    root = Path(args.project_root).resolve()
    store_dir = store_dir_from_env(args.store_dir)
    run_path = store_dir / "last_run.json"
    context = collect_release_context(root)
    package = {
        "task": "подготовка релиза проекта AiTgsterBot",
        "ai_role": "DeepSeek analyzes repository context and writes release notes/checklist/risks.",
        "branch": context.branch,
        "commit": context.commit,
        "changed_files": context.changed_files,
        "submission_files": [
            DEFAULT_DOC_PATH,
            DEFAULT_REPORT_PATH,
            "day35_release_prep.py",
            "day35_release_video.py",
            str(video_output_from_env(args.video_output)),
        ],
        "last_run_exists": run_path.exists(),
    }
    store_dir.mkdir(parents=True, exist_ok=True)
    (store_dir / "package.json").write_text(to_json(package), encoding="utf-8")
    print("day35_package")
    print(to_json(package))
    return 0


def command_video(args: argparse.Namespace) -> int:
    output = video_output_from_env(args.video_output)
    cmd = [sys.executable, "day35_release_video.py", "--output", str(output)]
    completed = subprocess.run(cmd, cwd=Path(args.project_root).resolve(), text=True)
    return completed.returncode


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--store-dir")
    parser.add_argument("--video-output")


def main() -> None:
    parser = argparse.ArgumentParser(description="Day35 real AI release prep pipeline.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect_parser = subparsers.add_parser("collect")
    add_common_args(collect_parser)
    collect_parser.set_defaults(func=command_collect)

    analyze_parser = subparsers.add_parser("analyze")
    add_common_args(analyze_parser)
    analyze_parser.set_defaults(func=command_analyze)

    write_parser = subparsers.add_parser("write")
    write_parser.add_argument("--apply", action="store_true")
    add_common_args(write_parser)
    write_parser.set_defaults(func=command_write)

    package_parser = subparsers.add_parser("package")
    add_common_args(package_parser)
    package_parser.set_defaults(func=command_package)

    video_parser = subparsers.add_parser("video")
    add_common_args(video_parser)
    video_parser.set_defaults(func=command_video)

    args = parser.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
