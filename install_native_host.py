#!/usr/bin/env python3
"""Registers the ytget Chrome Native Messaging host manifest so Chrome can launch the server.
Cross-platform: supports Linux, macOS, and Windows.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HOST_NAME = "com.ytget.server"
DEFAULT_EXT_ID = "obgihacdohhjonihcibbdfgmnmllogcb"
PROJECT_DIR = Path(__file__).resolve().parent

# Target dirs for POSIX (Linux and macOS)
POSIX_TARGET_DIRS = [
    # Linux
    Path.home() / ".config" / "google-chrome" / "NativeMessagingHosts",
    Path.home() / ".config" / "chromium" / "NativeMessagingHosts",
    Path.home() / ".config" / "google-chrome-beta" / "NativeMessagingHosts",
    Path.home() / ".config" / "google-chrome-unstable" / "NativeMessagingHosts",
    Path.home() / ".config" / "BraveSoftware" / "Brave-Browser" / "NativeMessagingHosts",
    Path.home() / ".config" / "microsoft-edge" / "NativeMessagingHosts",
    # macOS
    Path.home() / "Library" / "Application Support" / "Google" / "Chrome" / "NativeMessagingHosts",
    Path.home() / "Library" / "Application Support" / "Chromium" / "NativeMessagingHosts",
    Path.home() / "Library" / "Application Support" / "BraveSoftware" / "Brave-Browser" / "NativeMessagingHosts",
    Path.home() / "Library" / "Application Support" / "Microsoft Edge" / "NativeMessagingHosts",
]


def install_windows(ext_id: str) -> None:
    host_bat = PROJECT_DIR / "ytget_host.bat"
    if not host_bat.exists():
        print(f"Error: Host script not found: {host_bat}", file=sys.stderr)
        sys.exit(1)

    manifest_data = {
        "name": HOST_NAME,
        "description": "ytget local companion server launcher",
        "path": str(host_bat),
        "type": "stdio",
        "allowed_origins": [
            f"chrome-extension://{ext_id}/",
        ],
    }

    manifest_file = PROJECT_DIR / f"{HOST_NAME}.json"
    manifest_file.write_text(json.dumps(manifest_data, indent=2) + "\n")
    print(f"Wrote host manifest: {manifest_file}")

    # Register in Windows Registry
    import winreg

    registry_keys = [
        r"Software\Google\Chrome\NativeMessagingHosts",
        r"Software\Microsoft\Edge\NativeMessagingHosts",
        r"Software\BraveSoftware\Brave\NativeMessagingHosts",
    ]

    installed = 0
    for reg_base in registry_keys:
        try:
            sub_key_path = f"{reg_base}\\{HOST_NAME}"
            key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, sub_key_path)
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(manifest_file))
            winreg.CloseKey(key)
            print(f"Registered Windows Registry key: HKCU\\{sub_key_path}")
            installed += 1
        except Exception as exc:
            print(f"Notice: Could not write HKCU\\{reg_base}: {exc}")

    if installed > 0:
        print("\n✅ Native Messaging host registered successfully on Windows!")
        print(f"  • Host Name: {HOST_NAME}")
        print(f"  • Allowed Extension ID: {ext_id}")
        print(f"  • Host Script: {host_bat}")
        print(f"  • Manifest File: {manifest_file}")


def install_posix(ext_id: str) -> None:
    host_sh = PROJECT_DIR / "ytget_host.sh"
    if not host_sh.exists():
        print(f"Error: Host script not found: {host_sh}", file=sys.stderr)
        sys.exit(1)

    os.chmod(host_sh, 0o755)

    manifest_data = {
        "name": HOST_NAME,
        "description": "ytget local companion server launcher",
        "path": str(host_sh),
        "type": "stdio",
        "allowed_origins": [
            f"chrome-extension://{ext_id}/",
        ],
    }

    installed_count = 0
    for target_dir in POSIX_TARGET_DIRS:
        parent_browser_dir = target_dir.parent
        if parent_browser_dir.exists() or "google-chrome" in str(target_dir):
            target_dir.mkdir(parents=True, exist_ok=True)
            manifest_file = target_dir / f"{HOST_NAME}.json"
            manifest_file.write_text(json.dumps(manifest_data, indent=2) + "\n")
            print(f"Registered host manifest: {manifest_file}")
            installed_count += 1

    if installed_count > 0:
        print("\n✅ Native Messaging host registered successfully!")
        print(f"  • Host Name: {HOST_NAME}")
        print(f"  • Allowed Extension ID: {ext_id}")
        print(f"  • Target Script: {host_sh}")
    else:
        print("Warning: No browser directory found to install manifest.", file=sys.stderr)


def install(ext_id: str = DEFAULT_EXT_ID) -> None:
    if sys.platform == "win32":
        install_windows(ext_id)
    else:
        install_posix(ext_id)


def main():
    ext_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_EXT_ID
    install(ext_id)


if __name__ == "__main__":
    main()
