#!/usr/bin/env python3
"""Safe installer support: copy files, back up configs, and merge hook entries."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Any


PLUGIN_NAME = "ai-development-journal"


def read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else default
    except (OSError, ValueError):
        return default


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def backup_file(path: Path, backup_dir: Path, label: str) -> None:
    if not path.exists():
        return
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / label
    if path.is_dir():
        shutil.copytree(path, destination, dirs_exist_ok=True)
    else:
        shutil.copy2(path, destination)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def is_our_handler(handler: Any) -> bool:
    return isinstance(handler, dict) and "ai-development-journal" in str(handler.get("command", ""))


def remove_our_claude_hooks(settings: dict[str, Any]) -> bool:
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return False
    changed = False
    for event in list(hooks):
        groups = hooks.get(event)
        if not isinstance(groups, list):
            continue
        kept_groups: list[Any] = []
        for group in groups:
            if not isinstance(group, dict):
                kept_groups.append(group)
                continue
            handlers = group.get("hooks")
            if not isinstance(handlers, list):
                kept_groups.append(group)
                continue
            filtered = [handler for handler in handlers if not is_our_handler(handler)]
            if len(filtered) != len(handlers):
                changed = True
            if filtered:
                updated = dict(group)
                updated["hooks"] = filtered
                kept_groups.append(updated)
        if kept_groups:
            hooks[event] = kept_groups
        else:
            hooks.pop(event, None)
    if not hooks:
        settings.pop("hooks", None)
    return changed


def claude_hook_groups(script: Path) -> dict[str, list[dict[str, Any]]]:
    command = f'python3 "{script}" hook --source claude'

    def group(timeout: int = 5, matcher: str | None = None, status: str | None = None) -> dict[str, Any]:
        handler: dict[str, Any] = {"type": "command", "command": command, "timeout": timeout}
        if status:
            handler["statusMessage"] = status
        value: dict[str, Any] = {"hooks": [handler]}
        if matcher:
            value["matcher"] = matcher
        return value

    return {
        "SessionStart": [group()],
        "UserPromptSubmit": [group()],
        "PostToolUse": [group(matcher="Bash|Edit|Write|NotebookEdit")],
        "PreCompact": [group()],
        "Stop": [group(timeout=8)],
        "SessionEnd": [group(timeout=3)],
    }


def merge_claude_hooks(settings_path: Path, script: Path) -> None:
    settings = read_json(settings_path, {})
    remove_our_claude_hooks(settings)
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError(f"{settings_path}: hooks must be a JSON object")
    for event, groups in claude_hook_groups(script).items():
        current = hooks.setdefault(event, [])
        if not isinstance(current, list):
            raise ValueError(f"{settings_path}: hooks.{event} must be a JSON array")
        current.extend(groups)
    write_json(settings_path, settings)


def merge_marketplace(path: Path) -> str:
    marketplace = read_json(
        path,
        {"name": "personal", "interface": {"displayName": "Personal"}, "plugins": []},
    )
    name = marketplace.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"{path}: marketplace name must be a non-empty string")
    interface = marketplace.setdefault("interface", {"displayName": "Personal"})
    if not isinstance(interface, dict):
        raise ValueError(f"{path}: interface must be an object")
    interface.setdefault("displayName", name.replace("-", " ").title())
    plugins = marketplace.setdefault("plugins", [])
    if not isinstance(plugins, list):
        raise ValueError(f"{path}: plugins must be an array")
    entry = {
        "name": PLUGIN_NAME,
        "source": {"source": "local", "path": f"./plugins/{PLUGIN_NAME}"},
        "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
        "category": "Productivity",
    }
    replaced = False
    for index, existing in enumerate(plugins):
        if isinstance(existing, dict) and existing.get("name") == PLUGIN_NAME:
            plugins[index] = entry
            replaced = True
            break
    if not replaced:
        plugins.append(entry)
    write_json(path, marketplace)
    return name


def remove_marketplace_entry(path: Path) -> None:
    if not path.exists():
        return
    marketplace = read_json(path, {})
    plugins = marketplace.get("plugins")
    if isinstance(plugins, list):
        marketplace["plugins"] = [
            entry for entry in plugins
            if not (isinstance(entry, dict) and entry.get("name") == PLUGIN_NAME)
        ]
        write_json(path, marketplace)


def detect_vault(home: Path, existing: str = "") -> Path:
    if existing:
        candidate = Path(existing).expanduser()
        if candidate.is_dir():
            return candidate.resolve()
    env_value = os.environ.get("AI_DEV_JOURNAL_VAULT", "")
    if env_value:
        candidate = Path(env_value).expanduser()
        if candidate.is_dir():
            return candidate.resolve()
    candidates = [home / "Documents" / "Obsidian Vault"]
    candidates.extend(path.parent for path in (home / "Documents").glob("*/.obsidian"))
    icloud = home / "Library" / "Mobile Documents" / "iCloud~md~obsidian" / "Documents"
    candidates.extend(path.parent for path in icloud.glob("*/.obsidian"))
    unique = [path.resolve() for path in candidates if (path / ".obsidian").is_dir()]
    if unique:
        return unique[0]
    raise ValueError("No Obsidian vault found. Re-run install.sh --vault /path/to/vault")


def vault_backup_root(vault: Path, config: dict[str, Any]) -> Path:
    raw = str(config.get("backup_subdirectory") or "bak/AI Development Journal").strip()
    subdirectory = Path(raw)
    if subdirectory.is_absolute() or any(part in {"", ".", ".."} for part in subdirectory.parts):
        subdirectory = Path("bak") / "AI Development Journal"
    return vault / subdirectory / "plugin-backups"


def copy_plugin(source: Path, destination: Path) -> None:
    if source.resolve() == destination.resolve():
        return

    def ignore(_directory: str, names: list[str]) -> set[str]:
        return {name for name in names if name in {"__pycache__", ".DS_Store", ".git", ".ruff_cache"}}

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, dirs_exist_ok=True, ignore=ignore)
    for path in [destination / "install.sh", destination / "uninstall.sh", *list((destination / "scripts").glob("*.py"))]:
        if path.exists():
            path.chmod(path.stat().st_mode | stat.S_IXUSR)


def install(args: argparse.Namespace) -> dict[str, Any]:
    home = Path(args.home).expanduser().resolve()
    source = Path(args.source).expanduser().resolve()
    destination = home / "plugins" / PLUGIN_NAME
    settings = home / ".claude" / "settings.json"
    marketplace = home / ".agents" / "plugins" / "marketplace.json"
    codex_config = home / ".codex" / "config.toml"
    local_config = home / ".config" / PLUGIN_NAME / "config.json"
    example = read_json(source / "config.example.json", {})
    existing = read_json(local_config, {})
    merged = deep_merge(example, existing)
    try:
        existing_version = int(existing.get("version", 1))
        target_version = int(example.get("version", 1))
    except (TypeError, ValueError):
        existing_version, target_version = 1, 2
    if existing and existing_version < target_version:
        # Version 2 intentionally changes only journal organization and tags.
        # Vault, project naming, privacy settings, and gate thresholds stay intact.
        merged["version"] = target_version
        merged["layout"] = "date_first"
        merged["file_mode"] = "daily"
        merged["month_subdirectories"] = True
        merged["year_subdirectories"] = False
        merged["metadata"] = dict(example.get("metadata", {}))
    requested_vault = args.vault or str(merged.get("vault_path") or "")
    vault = detect_vault(home, requested_vault)
    merged["vault_path"] = str(vault)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = vault_backup_root(vault, merged) / stamp

    backup_file(settings, backup_dir, "claude-settings.json")
    backup_file(marketplace, backup_dir, "marketplace.json")
    backup_file(codex_config, backup_dir, "codex-config.toml")
    backup_file(local_config, backup_dir, "journal-config.json")
    if destination.exists() and destination.resolve() != source.resolve():
        backup_file(destination, backup_dir, "previous-plugin")

    copy_plugin(source, destination)
    old_skill = destination / "skills" / "record-development-checkpoint"
    if old_skill.is_dir() and (source / "skills" / "record-dev-notes").is_dir():
        shutil.rmtree(old_skill)
    marketplace_name = merge_marketplace(marketplace)
    merge_claude_hooks(settings, destination / "scripts" / "journal.py")

    write_json(local_config, merged)

    return {
        "plugin": str(destination),
        "marketplace": marketplace_name,
        "marketplace_path": str(marketplace),
        "claude_settings": str(settings),
        "config": str(local_config),
        "vault": str(vault),
        "backup": str(backup_dir),
    }


def uninstall(args: argparse.Namespace) -> dict[str, Any]:
    home = Path(args.home).expanduser().resolve()
    destination = home / "plugins" / PLUGIN_NAME
    settings_path = home / ".claude" / "settings.json"
    marketplace_path = home / ".agents" / "plugins" / "marketplace.json"
    local_config = home / ".config" / PLUGIN_NAME / "config.json"
    config = read_json(local_config, {})
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        vault = detect_vault(home, str(config.get("vault_path") or ""))
        backup_dir = vault_backup_root(vault, config) / f"uninstall-{stamp}"
    except ValueError:
        backup_dir = home / ".local" / "share" / PLUGIN_NAME / "backups" / f"uninstall-{stamp}"
    backup_file(settings_path, backup_dir, "claude-settings.json")
    backup_file(marketplace_path, backup_dir, "marketplace.json")

    settings = read_json(settings_path, {})
    if remove_our_claude_hooks(settings):
        write_json(settings_path, settings)
    remove_marketplace_entry(marketplace_path)

    removed_code = False
    manifest = destination / ".codex-plugin" / "plugin.json"
    manifest_value = read_json(manifest, {})
    if destination.is_dir() and manifest_value.get("name") == PLUGIN_NAME:
        shutil.rmtree(destination)
        removed_code = True

    if args.purge_state:
        config_dir = home / ".config" / PLUGIN_NAME
        state_dir = home / ".local" / "state" / PLUGIN_NAME
        if config_dir.is_dir():
            shutil.rmtree(config_dir)
        if state_dir.is_dir():
            shutil.rmtree(state_dir)

    return {
        "removed_plugin_code": removed_code,
        "removed_claude_hooks": True,
        "removed_marketplace_entry": True,
        "purged_config_and_state": bool(args.purge_state),
        "journal_notes_removed": False,
        "backup": str(backup_dir),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    install_parser = sub.add_parser("install")
    install_parser.add_argument("--source", required=True)
    install_parser.add_argument("--home", default=str(Path.home()))
    install_parser.add_argument("--vault")
    uninstall_parser = sub.add_parser("uninstall")
    uninstall_parser.add_argument("--home", default=str(Path.home()))
    uninstall_parser.add_argument("--purge-state", action="store_true")
    args = parser.parse_args()
    result = install(args) if args.command == "install" else uninstall(args)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
