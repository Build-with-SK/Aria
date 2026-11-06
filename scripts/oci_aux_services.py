"""Install loopback globe and daily state backup services on OCI."""
from pathlib import Path
import subprocess


def install(name, contents):
    subprocess.run(["sudo", "-n", "tee", f"/etc/systemd/system/{name}"],
                   input=contents, text=True, stdout=subprocess.DEVNULL, check=True)


def main():
    home = Path.home()
    backup = home / "ServerBackups"
    backup.mkdir(exist_ok=True, mode=0o700)
    install("gods-eye.service", f"""[Unit]
Description=Owner private globe
After=network-online.target
[Service]
User=ubuntu
WorkingDirectory={home}/apps/gods-eye-view
Environment=PUPPETEER_SKIP_DOWNLOAD=true
Environment=GEV_ALLOWED_HOSTS=gods-eye.localhost
Environment=GEV_KEY_SETUP_TRUSTED_PEERS=none
ExecStart={home}/apps/sentinel/venv/bin/python {home}/apps/sentinel/scripts/gods_eye_server.py --repo {home}/apps/gods-eye-view --node {home}/.local/toolchains/node-v24.22.2-linux-arm64/bin/node --port 4174
Restart=on-failure
RestartSec=5
KillMode=control-group
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
[Install]
WantedBy=multi-user.target
""")
    install("aria-state-backup.service", f"""[Unit]
Description=ARIA and Sentinel state backup
[Service]
Type=oneshot
User=ubuntu
WorkingDirectory={home}/apps/aria
Environment=SERVER_BACKUP_DIR={backup}
Environment=SENTINEL_HOME={home}/apps/sentinel
ExecStart={home}/apps/aria/venv/bin/python scripts/backup.py --keep 14
UMask=0077
Nice=10
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
""")
    install("aria-state-backup.timer", """[Unit]
Description=Daily owner state backup
[Timer]
OnCalendar=*-*-* 03:15:00
RandomizedDelaySec=300
Persistent=true
[Install]
WantedBy=timers.target
""")
    install("aria-pipeline.service", f"""[Unit]
Description=ARIA daily research and model pipeline
After=network-online.target
[Service]
Type=oneshot
User=ubuntu
WorkingDirectory={home}/apps/aria
EnvironmentFile={home}/.config/aria-sentinel/aria.env
ExecStart={home}/apps/aria/venv/bin/python main.py
UMask=0077
Nice=15
CPUWeight=10
CPUQuota=100%
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
TimeoutStartSec=infinity
""")
    install("aria-pipeline.timer", """[Unit]
Description=Preserved daily ARIA pipeline schedule (06:30 UTC)
[Timer]
OnCalendar=*-*-* 06:30:00 UTC
Persistent=true
[Install]
WantedBy=timers.target
""")
    # Reports remain in application data; the private Windows vault is not mounted.
    import yaml
    universe = home / "apps/aria/configs/universe.yaml"
    settings = yaml.safe_load(universe.read_text())
    settings.setdefault("obsidian", {})["vault_path"] = ""
    settings.setdefault("system", {})["pipeline_propose_trades"] = False
    universe.write_text(yaml.safe_dump(settings, sort_keys=False))
    config = home / "apps/sentinel/data/sentinel_config.json"
    import json
    data = json.loads(config.read_text())
    data["browser_use_repo"] = str(home / "apps/browser-use")
    config.write_text(json.dumps(data, indent=2))
    config.chmod(0o600)
    subprocess.run(["sudo", "-n", "systemctl", "daemon-reload"], check=True)
    subprocess.run(["sudo", "-n", "systemctl", "enable", "--now", "gods-eye", "aria-state-backup.timer", "aria-pipeline.timer"], check=True)


if __name__ == "__main__":
    main()
