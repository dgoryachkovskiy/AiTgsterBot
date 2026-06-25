import argparse
import os
import posixpath
import shlex
import sys
import time
from pathlib import Path

from dotenv import load_dotenv


DEFAULT_HOST = "138.16.168.37"
DEFAULT_USER = "root"
DEFAULT_APP_DIR = "/opt/day18-worldcup-mcp"
DEFAULT_PORT = 8002
DEFAULT_REFRESH_SECONDS = 7200
SERVICE_NAME = "day18-worldcup-mcp"
REMOTE_REQUIREMENTS = "\n".join(["mcp>=1.28,<2", "httpx>=0.28,<1", ""])


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def load_settings() -> dict[str, str | int]:
    load_dotenv()
    return {
        "host": os.getenv("DAY18_VPS_HOST", DEFAULT_HOST),
        "user": os.getenv("DAY18_VPS_USER", DEFAULT_USER),
        "password": require_env("DAY18_VPS_PASSWORD"),
        "app_dir": os.getenv("DAY18_VPS_APP_DIR", DEFAULT_APP_DIR),
        "port": int(os.getenv("DAY18_MCP_PORT", str(DEFAULT_PORT))),
        "refresh_seconds": int(os.getenv("DAY18_REFRESH_SECONDS", str(DEFAULT_REFRESH_SECONDS))),
    }


def connect(settings: dict[str, str | int]):
    import paramiko

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=str(settings["host"]),
        username=str(settings["user"]),
        password=str(settings["password"]),
        timeout=20,
        banner_timeout=20,
        auth_timeout=20,
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


def upload_files(client, settings: dict[str, str | int]) -> None:
    app_dir = str(settings["app_dir"])
    run(client, f"mkdir -p {shlex.quote(app_dir)}")
    sftp = client.open_sftp()
    try:
        sftp.put(str(Path("day18_worldcup_mcp_server.py")), posixpath.join(app_dir, "day18_worldcup_mcp_server.py"))
        write_remote_file(sftp, posixpath.join(app_dir, "requirements.txt"), REMOTE_REQUIREMENTS)
    finally:
        sftp.close()


def service_unit(settings: dict[str, str | int]) -> str:
    app_dir = str(settings["app_dir"])
    port = int(settings["port"])
    refresh_seconds = int(settings["refresh_seconds"])
    store_dir = posixpath.join(app_dir, "data")
    return f"""[Unit]
Description=Day 18 World Cup scheduled MCP server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={app_dir}
ExecStart={app_dir}/.venv/bin/python {app_dir}/day18_worldcup_mcp_server.py --refresh-seconds {refresh_seconds} serve --host 0.0.0.0 --port {port}
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1
Environment=DAY18_STORE_DIR={store_dir}
Environment=DAY18_REFRESH_SECONDS={refresh_seconds}

[Install]
WantedBy=multi-user.target
"""


def install_service(client, settings: dict[str, str | int]) -> None:
    app_dir = str(settings["app_dir"])
    app_dir_q = shlex.quote(app_dir)
    service_path = f"/etc/systemd/system/{SERVICE_NAME}.service"
    run(client, "python3 --version")
    venv_package = run(client, "python3 -c 'import sys; print(f\"python{sys.version_info.major}.{sys.version_info.minor}-venv\")'").strip()
    run(
        client,
        (
            "apt-get update && "
            f"(DEBIAN_FRONTEND=noninteractive apt-get install -y {shlex.quote(venv_package)} python3-pip || "
            "DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv python3-pip)"
        ),
        timeout=600,
    )
    run(client, f"cd {app_dir_q} && python3 -m venv --clear .venv")
    run(client, f"cd {app_dir_q} && .venv/bin/python -m pip install --upgrade pip && .venv/bin/python -m pip install -r requirements.txt", timeout=600)
    sftp = client.open_sftp()
    try:
        write_remote_file(sftp, service_path, service_unit(settings))
    finally:
        sftp.close()
    run(client, "systemctl daemon-reload")
    run(client, f"systemctl enable {SERVICE_NAME}")
    run(client, f"systemctl restart {SERVICE_NAME}")
    time.sleep(4)
    run(client, f"systemctl --no-pager --full status {SERVICE_NAME}", timeout=60)
    run(client, f"ls -la {app_dir_q}/data || true")


def verify_remote(client, settings: dict[str, str | int]) -> None:
    port = int(settings["port"])
    run(client, f"curl -i --max-time 10 http://127.0.0.1:{port}/mcp | head -n 20")


def deploy() -> None:
    settings = load_settings()
    print(f"deploy_host={settings['host']}")
    print(f"deploy_app_dir={settings['app_dir']}")
    client = connect(settings)
    try:
        upload_files(client, settings)
        install_service(client, settings)
        verify_remote(client, settings)
    finally:
        client.close()
    print(f"remote_mcp_url=http://{settings['host']}:{settings['port']}/mcp")
    print("deploy_done=True")


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy Day 18 scheduled World Cup MCP server to VPS.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("deploy", help="copy files, install systemd service, start scheduler server")
    args = parser.parse_args()
    if args.command == "deploy":
        deploy()


if __name__ == "__main__":
    main()
