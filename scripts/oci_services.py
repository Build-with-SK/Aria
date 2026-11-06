"""Install private, unprivileged systemd services for the staged OCI apps."""
from pathlib import Path
import subprocess


def main():
    for app, module, port in (("aria", "backend.main:app", 8000), ("sentinel", "sentinel.server:app", 8300)):
        root = Path.home() / "apps" / app
        unit = f"""[Unit]
Description={app.upper()} owner cloud application
After=network-online.target ollama.service
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory={root}
EnvironmentFile={Path.home()}/.config/aria-sentinel/{app}.env
ExecStart={root}/venv/bin/python {Path.home()}/deploy/oci_serve.py {app}
Restart=on-failure
RestartSec=5
TimeoutStopSec=45
KillMode=control-group
UMask=0077
Nice=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
LimitNOFILE=8192

[Install]
WantedBy=multi-user.target
"""
        path = f"/etc/systemd/system/{app}.service"
        subprocess.run(["sudo", "-n", "tee", path], input=unit, text=True, stdout=subprocess.DEVNULL, check=True)
    subprocess.run(["sudo", "-n", "systemctl", "daemon-reload"], check=True)
    subprocess.run(["sudo", "-n", "systemctl", "enable", "aria", "sentinel"], check=True)
    subprocess.run(["sudo", "-n", "systemctl", "start", "aria", "sentinel"], check=True)
    subprocess.run(["systemctl", "is-active", "aria", "sentinel"], check=True)


if __name__ == "__main__":
    main()
