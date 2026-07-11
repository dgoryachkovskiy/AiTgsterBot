import argparse
import json
import os
import posixpath
import secrets
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


DEFAULT_HOST = "138.16.168.37"
DEFAULT_USER = "root"
DEFAULT_APP_DIR = "/opt/day30-private-llm-service"
DEFAULT_PORT = 8010
DEFAULT_MODEL = "qwen2.5:0.5b"
SERVICE_NAME = "day30-private-llm"
OLLAMA_SERVICE_NAME = "day30-ollama"
STORE_DIR = "day30_private_llm_store"
REMOTE_REQUIREMENTS = "\n".join(["fastapi>=0.115,<1", "uvicorn[standard]>=0.30,<1", "httpx>=0.28,<1", "pydantic>=2,<3", ""])


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def first_env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


def store_dir() -> Path:
    return Path(os.getenv("DAY30_STORE_DIR", STORE_DIR)).resolve()


def load_settings() -> dict[str, Any]:
    load_dotenv()
    api_key = os.getenv("DAY30_API_KEY")
    generated = False
    if not api_key or api_key == "replace_me":
        api_key = "day30_" + secrets.token_urlsafe(32)
        generated = True
    host = first_env("DAY30_VPS_HOST", "DAY19_VPS_HOST", "DAY18_VPS_HOST", "DAY17_VPS_HOST", default=DEFAULT_HOST)
    port = int(os.getenv("DAY30_PORT", str(DEFAULT_PORT)))
    return {
        "host": host,
        "user": first_env("DAY30_VPS_USER", "DAY19_VPS_USER", "DAY18_VPS_USER", "DAY17_VPS_USER", default=DEFAULT_USER),
        "password": first_env("DAY30_VPS_PASSWORD", "DAY19_VPS_PASSWORD", "DAY18_VPS_PASSWORD", "DAY17_VPS_PASSWORD", default=""),
        "app_dir": os.getenv("DAY30_VPS_APP_DIR", DEFAULT_APP_DIR),
        "port": port,
        "model": os.getenv("DAY30_MODEL", DEFAULT_MODEL),
        "api_key": api_key,
        "api_key_generated": generated,
        "service_url": os.getenv("DAY30_SERVICE_URL", f"http://{host}:{port}"),
        "rate_limit": int(os.getenv("DAY30_RATE_LIMIT_PER_MINUTE", "10")),
        "max_messages": int(os.getenv("DAY30_MAX_MESSAGES", "12")),
        "max_input_chars": int(os.getenv("DAY30_MAX_INPUT_CHARS", "12000")),
    }


def connect(settings: dict[str, Any]):
    import paramiko

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=str(settings["host"]),
        username=str(settings["user"]),
        password=str(settings["password"]) or None,
        timeout=60,
        banner_timeout=60,
        auth_timeout=60,
        allow_agent=True,
        look_for_keys=True,
    )
    return client


def run(client, command: str, timeout: int = 300) -> str:
    print(f"$ {command}")
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    exit_code = stdout.channel.recv_exit_status()
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    if out.strip():
        print(out.rstrip())
    if err.strip():
        print(err.rstrip())
    if exit_code != 0:
        raise RuntimeError(f"Remote command failed with exit code {exit_code}: {command}")
    return out


def write_remote_file(sftp, remote_path: str, content: str) -> None:
    with sftp.open(remote_path, "w") as remote_file:
        remote_file.write(content)


def upload_files(client, settings: dict[str, Any]) -> None:
    app_dir = str(settings["app_dir"])
    static_dir = posixpath.join(app_dir, "day30_web_static")
    run(client, f"mkdir -p {shlex.quote(app_dir)}")
    run(client, f"mkdir -p {shlex.quote(static_dir)}")
    sftp = client.open_sftp()
    try:
        sftp.put(str(Path("day30_private_llm_service.py")), posixpath.join(app_dir, "day30_private_llm_service.py"))
        for local_file in Path("day30_web_static").iterdir():
            if local_file.is_file():
                sftp.put(str(local_file), posixpath.join(static_dir, local_file.name))
        write_remote_file(sftp, posixpath.join(app_dir, "requirements.txt"), REMOTE_REQUIREMENTS)
        write_remote_file(
            sftp,
            posixpath.join(app_dir, "day30.env"),
            "\n".join(
                [
                    f"DAY30_API_KEY={settings['api_key']}",
                    f"DAY30_MODEL={settings['model']}",
                    "DAY30_OLLAMA_URL=http://127.0.0.1:11434",
                    f"DAY30_RATE_LIMIT_PER_MINUTE={settings['rate_limit']}",
                    f"DAY30_MAX_MESSAGES={settings['max_messages']}",
                    f"DAY30_MAX_INPUT_CHARS={settings['max_input_chars']}",
                    "DAY30_NUM_CTX=4096",
                    "DAY30_NUM_PREDICT=256",
                    "",
                ]
            ),
        )
    finally:
        sftp.close()
    run(client, f"chmod 600 {shlex.quote(posixpath.join(app_dir, 'day30.env'))}")


def ollama_service_unit() -> str:
    return """[Unit]
Description=Day 30 private Ollama service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/root
Environment=HOME=/root
Environment=OLLAMA_HOST=127.0.0.1:11434
Environment=OLLAMA_MODELS=/root/.ollama/models
ExecStart=/usr/local/bin/ollama serve
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
"""


def private_service_unit(settings: dict[str, Any]) -> str:
    app_dir = str(settings["app_dir"])
    port = int(settings["port"])
    return f"""[Unit]
Description=Day 30 private local LLM HTTP API
After=network-online.target {OLLAMA_SERVICE_NAME}.service
Wants=network-online.target {OLLAMA_SERVICE_NAME}.service

[Service]
Type=simple
WorkingDirectory={app_dir}
EnvironmentFile={app_dir}/day30.env
Environment=PYTHONUNBUFFERED=1
ExecStart={app_dir}/.venv/bin/uvicorn day30_private_llm_service:app --host 0.0.0.0 --port {port}
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
"""


def install_remote(client, settings: dict[str, Any]) -> None:
    app_dir = str(settings["app_dir"])
    app_dir_q = shlex.quote(app_dir)
    run(client, "apt-get update", timeout=600)
    run(client, "DEBIAN_FRONTEND=noninteractive apt-get install -y curl ca-certificates python3-venv python3-pip", timeout=600)
    run(
        client,
        (
            "if ! command -v ollama >/dev/null 2>&1 || [ ! -x /usr/local/lib/ollama/llama-server ]; then "
            "curl -fsSL https://ollama.com/install.sh | sh; "
            "fi"
        ),
        timeout=900,
    )
    run(client, "systemctl disable --now ollama || true")
    sftp = client.open_sftp()
    try:
        write_remote_file(sftp, f"/etc/systemd/system/{OLLAMA_SERVICE_NAME}.service", ollama_service_unit())
        write_remote_file(sftp, f"/etc/systemd/system/{SERVICE_NAME}.service", private_service_unit(settings))
    finally:
        sftp.close()
    run(client, "systemctl daemon-reload")
    run(client, f"systemctl enable --now {OLLAMA_SERVICE_NAME}", timeout=60)
    time.sleep(4)
    run(client, "curl --fail --max-time 20 http://127.0.0.1:11434/api/tags >/dev/null")
    run(client, f"OLLAMA_HOST=127.0.0.1:11434 ollama pull {shlex.quote(settings['model'])}", timeout=900)
    run(client, f"cd {app_dir_q} && python3 -m venv --clear .venv")
    run(client, f"cd {app_dir_q} && .venv/bin/python -m pip install --upgrade pip && .venv/bin/python -m pip install -r requirements.txt", timeout=600)
    run(client, f"systemctl enable {SERVICE_NAME}")
    run(client, f"systemctl restart {SERVICE_NAME}", timeout=60)
    time.sleep(4)
    run(client, f"systemctl --no-pager --full status {OLLAMA_SERVICE_NAME}", timeout=60)
    run(client, f"systemctl --no-pager --full status {SERVICE_NAME}", timeout=60)
    run(client, "ss -ltnp | grep -E '(:11434|:8010)' || true")
    run(client, "command -v ufw >/dev/null 2>&1 && ufw allow 8010/tcp || true")


def save_deploy_info(settings: dict[str, Any]) -> Path:
    store_dir().mkdir(parents=True, exist_ok=True)
    path = store_dir() / "deploy.json"
    path.write_text(
        json.dumps(
            {
                "service_url": settings["service_url"],
                "api_key": settings["api_key"],
                "host": settings["host"],
                "port": settings["port"],
                "model": settings["model"],
                "api_key_generated": settings["api_key_generated"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path.resolve()


def verify_local(settings: dict[str, Any]) -> None:
    command = [
        sys.executable,
        "day30_private_llm_client.py",
        "health",
        "--url",
        str(settings["service_url"]),
        "--api-key",
        str(settings["api_key"]),
    ]
    subprocess.run(command, check=True)
    subprocess.run(
        [
            sys.executable,
            "day30_private_llm_client.py",
            "chat",
            "Ответь одним предложением: сервис доступен?",
            "--url",
            str(settings["service_url"]),
            "--api-key",
            str(settings["api_key"]),
        ],
        check=True,
    )


def deploy() -> None:
    settings = load_settings()
    print(f"deploy_host={settings['host']}")
    print(f"deploy_app_dir={settings['app_dir']}")
    print(f"deploy_service_url={settings['service_url']}")
    try:
        client = connect(settings)
    except Exception as error:
        print("deploy_connected=False")
        print(f"ssh_error={type(error).__name__}: {error}")
        print("set DAY30_VPS_PASSWORD in .env or configure an SSH key for root@138.16.168.37, then run deploy again")
        raise SystemExit(1)
    try:
        upload_files(client, settings)
        install_remote(client, settings)
    finally:
        client.close()
    deploy_path = save_deploy_info(settings)
    verify_local(settings)
    print(f"deploy_info_saved={deploy_path}")
    print("deploy_done=True")


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Deploy Day30 private local LLM HTTP service to VPS.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("deploy")
    args = parser.parse_args()
    if args.command == "deploy":
        deploy()


if __name__ == "__main__":
    main()
