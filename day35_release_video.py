from __future__ import annotations

import argparse
import os
import subprocess
import sys
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
import imageio_ffmpeg


DEFAULT_OUTPUT = r"C:\Users\pospi\Desktop\курс\7 неделя\day35_real_task.mp4"
WIDTH = 1280
HEIGHT = 720
FPS = 1


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def run_capture(command: list[str], cwd: Path, max_chars: int = 4200) -> str:
    completed = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    output = completed.stdout.strip()
    if len(output) > max_chars:
        output = output[:max_chars] + "\n...truncated..."
    return f"$ {' '.join(command)}\nexit={completed.returncode}\n{output}"


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        r"C:\Windows\Fonts\consola.ttf",
        r"C:\Windows\Fonts\arial.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def frame(text: str, title: str) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), "#10131a")
    draw = ImageDraw.Draw(image)
    title_font = load_font(30)
    body_font = load_font(19)
    small_font = load_font(16)
    draw.rectangle((0, 0, WIDTH, 72), fill="#182033")
    draw.text((32, 20), title, fill="#e8f0ff", font=title_font)
    draw.text((32, 92), "Real task: AI release prep for AiTgsterBot", fill="#8fb3ff", font=small_font)
    y = 128
    for raw_line in text.splitlines():
        wrapped = textwrap.wrap(raw_line, width=118, replace_whitespace=False) or [""]
        for line in wrapped:
            if y > HEIGHT - 42:
                draw.text((32, y), "...", fill="#d8dee9", font=body_font)
                return image
            color = "#79d7ff" if line.startswith("$ ") else "#d8dee9"
            if line.startswith("exit=0"):
                color = "#8bd986"
            elif line.startswith("exit="):
                color = "#ffb86c"
            draw.text((32, y), line, fill=color, font=body_font)
            y += 25
    return image


def write_video(frames: list[Image.Image], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    command = [
        ffmpeg,
        "-y",
        "-f",
        "rawvideo",
        "-vcodec",
        "rawvideo",
        "-s",
        f"{WIDTH}x{HEIGHT}",
        "-pix_fmt",
        "rgb24",
        "-r",
        str(FPS),
        "-i",
        "-",
        "-an",
        "-vcodec",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(output),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    assert process.stdin is not None
    try:
        for img in frames:
            for _ in range(3):
                process.stdin.write(img.tobytes())
    finally:
        process.stdin.close()
        process.wait(timeout=60)
    if process.returncode != 0:
        raise RuntimeError(f"ffmpeg failed with exit code {process.returncode}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create Day35 terminal-output MP4.")
    parser.add_argument("--output", default=os.getenv("DAY35_VIDEO_OUTPUT", DEFAULT_OUTPUT))
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    output = Path(args.output).resolve()

    commands = [
        ["day35_release_prep.py", "collect"],
        ["day35_release_prep.py", "analyze"],
        ["day35_release_prep.py", "write", "--apply"],
        ["day35_release_prep.py", "package"],
        ["git", "diff", "--stat"],
    ]
    rendered = []
    for command in commands:
        full = [sys.executable, *command] if command[0].endswith(".py") else command
        rendered.append(run_capture(full, root))
    rendered.append(
        "\n".join(
            [
                "Task solved: release preparation for AiTgsterBot.",
                "AI role: DeepSeek reads real repo context and writes release notes, risks, checklist.",
                f"Video output: {output}",
            ]
        )
    )
    frames = [frame(text, f"Day35 Step {index}") for index, text in enumerate(rendered, start=1)]
    write_video(frames, output)
    print(f"video_saved={output}")


if __name__ == "__main__":
    main()
