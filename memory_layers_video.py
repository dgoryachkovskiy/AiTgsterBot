import os
import re
import subprocess
import textwrap
from pathlib import Path

import imageio.v2 as imageio
import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
REPORT_PATH = ROOT / "MEMORY_LAYERS_REPORT.md"
VIDEO_PATH = Path(r"C:\Users\pospi\Desktop\курс\3 неделя\memory_layers_demo.mp4")
WIDTH = 1280
HEIGHT = 720
FPS = 1
HOLD_SECONDS = 3


def run_command(args: list[str]) -> tuple[str, str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    printable = " ".join(args)
    completed = subprocess.run(
        args,
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=300,
        env=env,
    )
    output = completed.stdout
    if completed.stderr:
        output += "\nSTDERR:\n" + completed.stderr
    output += f"\nexit_code={completed.returncode}"
    return printable, output.strip()


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        r"C:\Windows\Fonts\consola.ttf",
        r"C:\Windows\Fonts\arial.ttf",
    ]
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


TITLE_FONT = font(30)
BODY_FONT = font(18)
SMALL_FONT = font(15)


def clean_text(text: str) -> str:
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    return text.replace("\r\n", "\n").replace("\r", "\n")


def wrapped_lines(text: str, width: int = 112) -> list[str]:
    lines: list[str] = []
    for raw_line in clean_text(text).splitlines():
        if not raw_line:
            lines.append("")
            continue
        wrapped = textwrap.wrap(raw_line, width=width, replace_whitespace=False, drop_whitespace=False)
        lines.extend(wrapped or [""])
    return lines


def paginate(text: str, max_lines: int = 27) -> list[list[str]]:
    lines = wrapped_lines(text)
    return [lines[index : index + max_lines] for index in range(0, len(lines), max_lines)] or [[]]


def render_frame(title: str, subtitle: str, lines: list[str]) -> np.ndarray:
    image = Image.new("RGB", (WIDTH, HEIGHT), "#101418")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, WIDTH, 86), fill="#18202a")
    draw.text((36, 20), title, fill="#f5f7fa", font=TITLE_FONT)
    draw.text((36, 58), subtitle, fill="#a8c7ff", font=SMALL_FONT)

    y = 112
    for line in lines:
        color = "#d7dde8"
        if line.startswith("###") or line.startswith("# "):
            color = "#7ee787"
        elif "ERROR" in line or "exit_code=1" in line:
            color = "#ff7b72"
        elif "prompt=" in line or "cost~" in line or "total=" in line:
            color = "#f2cc60"
        draw.text((36, y), line, fill=color, font=BODY_FONT)
        y += 21
        if y > HEIGHT - 42:
            break

    draw.text((36, HEIGHT - 30), "Memory Layers Agent + DeepSeek API", fill="#6e7681", font=SMALL_FONT)
    return np.asarray(image)


def add_text_section(frames: list[np.ndarray], title: str, subtitle: str, text: str) -> None:
    pages = paginate(text)
    for page_index, page in enumerate(pages, start=1):
        page_subtitle = f"{subtitle} | page {page_index}/{len(pages)}" if len(pages) > 1 else subtitle
        frame = render_frame(title, page_subtitle, page)
        for _ in range(HOLD_SECONDS):
            frames.append(frame)


def extract_report_comparison() -> str:
    if not REPORT_PATH.exists():
        return "Report was not generated."
    report = REPORT_PATH.read_text(encoding="utf-8")
    wanted = []
    for section in ("### all_layers", "### short_term_only"):
        start = report.find(section)
        if start == -1:
            continue
        next_start = report.find("\n### ", start + 1)
        if next_start == -1:
            next_start = report.find("\n## ", start + 1)
        wanted.append(report[start:next_start].strip())
    return "\n\n".join(wanted) if wanted else "Comparison sections not found in report."


def main() -> None:
    VIDEO_PATH.parent.mkdir(parents=True, exist_ok=True)
    frames: list[np.ndarray] = []

    add_text_section(
        frames,
        "Memory Layers Agent",
        "video goal",
        "\n".join(
            [
                "Задача: показать явную модель памяти ассистента.",
                "Слои: short_term, working, long_term.",
                "Проверка: реальные запросы DeepSeek API.",
                f"Видео будет сохранено: {VIDEO_PATH}",
            ]
        ),
    )

    commands = [
        [str(PYTHON), "memory_layers_agent.py", "demo"],
        [str(PYTHON), "memory_layers_agent.py", "show", "--layers", "short_term"],
        [str(PYTHON), "memory_layers_agent.py", "show", "--layers", "working"],
        [str(PYTHON), "memory_layers_agent.py", "show", "--layers", "long_term"],
    ]
    for command in commands:
        printable, output = run_command(command)
        add_text_section(frames, "Command output", printable, output)

    add_text_section(frames, "Result comparison", "all_layers vs short_term_only", extract_report_comparison())
    add_text_section(
        frames,
        "Final result",
        "files",
        "\n".join(
            [
                f"Report: {REPORT_PATH}",
                f"Video: {VIDEO_PATH}",
                "all_layers: full profile + task + project memory.",
                "without_working: loses active task state.",
                "without_long_term: loses profile and stable decisions.",
                "short_term_only: only current dialog remains.",
            ]
        ),
    )

    with imageio.get_writer(VIDEO_PATH, fps=FPS, codec="libx264", quality=8) as writer:
        for frame in frames:
            writer.append_data(frame)

    print(f"video_saved:{VIDEO_PATH}")


if __name__ == "__main__":
    main()
