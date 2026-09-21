from copy import deepcopy
import ctypes
from pathlib import Path
import re
import string


DEFAULT_PROGRAM_CONFIG = {
    "executable_name": "BlueArchive.exe",
    "manual_path": "",
    "search_paths": [
        "Program Files (x86)/Steam/steamapps/common/BlueArchive/BlueArchive.exe",
        "SteamLibrary/steamapps/common/BlueArchive/BlueArchive.exe",
        # 요청에 명시된 철자도 기존 사용자 설치 경로 호환을 위해 함께 탐색한다.
        "SteamLibary/steamapps/common/BlueArchive/BlueArchive.exe",
    ],
    "startup_wait_seconds": 60,
    "recognition_timeout_seconds": 180,
    "poll_interval_seconds": 1.0,
}


def normalize_program_config(raw_config):
    config = deepcopy(DEFAULT_PROGRAM_CONFIG)
    if not isinstance(raw_config, dict):
        return config

    executable_name = raw_config.get("executable_name")
    if isinstance(executable_name, str) and executable_name.strip():
        config["executable_name"] = executable_name.strip()

    manual_path = raw_config.get("manual_path")
    if isinstance(manual_path, str):
        config["manual_path"] = manual_path.strip()

    search_paths = raw_config.get("search_paths")
    if (
        isinstance(search_paths, list)
        and search_paths
        and all(isinstance(path, str) and path.strip() for path in search_paths)
    ):
        config["search_paths"] = [path.strip() for path in search_paths]

    for key in ("startup_wait_seconds", "recognition_timeout_seconds"):
        value = raw_config.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            config[key] = int(value)

    poll_interval = raw_config.get("poll_interval_seconds")
    if (
        isinstance(poll_interval, (int, float))
        and not isinstance(poll_interval, bool)
        and poll_interval > 0
    ):
        config["poll_interval_seconds"] = float(poll_interval)
    return config


def windows_drive_roots():
    if not hasattr(ctypes, "windll"):
        return []
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    return [
        Path(f"{letter}:\\")
        for index, letter in enumerate(string.ascii_uppercase)
        if mask & (1 << index)
    ]


def resolve_manual_program_path(manual_path, executable_name):
    if not isinstance(manual_path, str) or not manual_path.strip():
        return None
    candidate = Path(manual_path.strip()).expanduser()
    if candidate.is_dir():
        candidate = candidate / executable_name
    if not candidate.is_file():
        return None
    if candidate.name.casefold() != executable_name.casefold():
        return None
    return candidate.resolve()


def find_auto_program_executable(program_config, drive_roots=None):
    config = normalize_program_config(program_config)
    roots = windows_drive_roots() if drive_roots is None else list(drive_roots)
    for root in roots:
        for relative_path in config["search_paths"]:
            candidate = Path(root) / Path(relative_path)
            if candidate.is_file() and candidate.name.casefold() == config[
                "executable_name"
            ].casefold():
                return candidate.resolve()
    return None


def find_program_executable(program_config, drive_roots=None):
    config = normalize_program_config(program_config)
    manual = resolve_manual_program_path(
        config["manual_path"], config["executable_name"]
    )
    if manual is not None:
        return manual
    return find_auto_program_executable(config, drive_roots)


def _read_steam_manifest_value(manifest_text, key):
    match = re.search(
        rf'^\s*"{re.escape(key)}"\s+"([^"]*)"',
        manifest_text,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    if match is None:
        return None
    return match.group(1).replace("\\\\", "\\")


def find_steam_launch(executable_path):
    """Return the Steam launcher and app URL for an executable in a Steam library."""
    executable = Path(executable_path).resolve()
    parents = list(executable.parents)
    steamapps_dir = next(
        (parent for parent in parents if parent.name.casefold() == "steamapps"),
        None,
    )
    if steamapps_dir is None:
        return None

    common_dir = steamapps_dir / "common"
    try:
        executable.relative_to(common_dir)
    except ValueError:
        return None

    for manifest_path in sorted(steamapps_dir.glob("appmanifest_*.acf")):
        try:
            manifest_text = manifest_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        app_id = _read_steam_manifest_value(manifest_text, "appid")
        install_dir = _read_steam_manifest_value(manifest_text, "installdir")
        launcher_path = _read_steam_manifest_value(manifest_text, "LauncherPath")
        if not app_id or not app_id.isdecimal() or not install_dir or not launcher_path:
            continue

        installed_root = (common_dir / install_dir).resolve()
        try:
            executable.relative_to(installed_root)
        except ValueError:
            continue

        launcher = Path(launcher_path).resolve()
        if not launcher.is_file():
            continue
        return launcher, f"steam://run/{app_id}"
    return None


def build_program_launch_command(executable_path):
    executable = Path(executable_path).resolve()
    steam_launch = find_steam_launch(executable)
    if steam_launch is not None:
        launcher, app_url = steam_launch
        return [str(launcher), app_url], launcher.parent
    return [str(executable)], executable.parent
