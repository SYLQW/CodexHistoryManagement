#!/usr/bin/env python3
"""
Codex Desktop sidebar and provider repair tool.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import shutil
import sqlite3
import threading
import tomllib
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

try:
    import tkinter as tk
    from tkinter import messagebox
    from tkinter import ttk
except Exception:  # pragma: no cover
    tk = None
    ttk = None
    messagebox = None


SIDEBAR_FILTER_KEY = "sidebar-workspace-filter-v2"
SIDEBAR_GROUP_KEY = "sidebar-organize-mode-v1"
SIDEBAR_COLLAPSED_KEY = "sidebar-collapsed-groups"
MODEL_PROVIDER_KEY = "model_provider"


@dataclass
class SidebarRepairResult:
    changed: bool
    backup_path: str | None
    root_count: int
    roots: list[str]
    message: str


@dataclass
class ProviderSyncResult:
    changed: bool
    backup_dir: str | None
    target_provider: str
    scanned_rollouts: int
    rollout_files_updated: int
    sqlite_rows_updated: int
    config_updated: bool
    message: str


def default_codex_home() -> Path:
    home = os.environ.get("CODEX_HOME")
    if home:
        return Path(home).expanduser()
    return Path.home() / ".codex"


def normalize_root(path_value: str | None) -> str | None:
    if not path_value:
        return None

    normalized = str(path_value).strip()
    if normalized.startswith("\\\\?\\"):
        normalized = normalized[4:]

    normalized = normalized.rstrip("\\/")
    if not normalized:
        return None

    if len(normalized) < 3 or normalized[1:3] != ":\\":
        return None

    return normalized


def ordered_unique(candidates: list[str | None]) -> list[str]:
    seen: set[str] = set()
    results: list[str] = []
    for candidate in candidates:
        normalized = normalize_root(candidate)
        if normalized and normalized.lower() not in seen:
            seen.add(normalized.lower())
            results.append(normalized)
    return results


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8-sig") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    return data


def save_json(path: Path, data: dict) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))


def ensure_backup_dir(codex_home: Path, name: str) -> Path:
    backup_root = codex_home / "backups_state"
    backup_root.mkdir(parents=True, exist_ok=True)
    backup_dir = backup_root / f"{name}-{datetime.now():%Y%m%d-%H%M%S}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir


def backup_file(path: Path, codex_home: Path, backup_dir: Path) -> Path:
    try:
        relative = path.relative_to(codex_home)
    except ValueError:
        relative = Path(path.name)
    destination = backup_dir / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, destination)
    return destination


def get_roots_from_database(db_path: Path) -> list[str]:
    if not db_path.exists():
        raise FileNotFoundError(f"SQLite file not found: {db_path}")

    query = """
    WITH ranked AS (
      SELECT
        cwd,
        updated_at,
        ROW_NUMBER() OVER (PARTITION BY cwd ORDER BY updated_at DESC) AS rn
      FROM threads
      WHERE archived = 0
        AND cwd IS NOT NULL
        AND TRIM(cwd) != ''
    )
    SELECT cwd
    FROM ranked
    WHERE rn = 1
    ORDER BY updated_at DESC
    """

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(query).fetchall()

    return ordered_unique([row[0] for row in rows])


def get_existing_roots(state: dict) -> list[str]:
    candidates: list[str | None] = []
    for key in (
        "electron-saved-workspace-roots",
        "project-order",
        "active-workspace-roots",
    ):
        value = state.get(key, [])
        if isinstance(value, list):
            candidates.extend(value)
    return ordered_unique(candidates)


def build_root_labels(roots: list[str], existing_labels: dict | None) -> dict[str, str]:
    labels: OrderedDict[str, str] = OrderedDict()
    if isinstance(existing_labels, dict):
        for raw_key, raw_value in existing_labels.items():
            normalized_key = normalize_root(raw_key)
            if (
                normalized_key
                and normalized_key in roots
                and isinstance(raw_value, str)
                and raw_value.strip()
            ):
                labels[normalized_key] = raw_value.strip()

    for root in roots:
        if root not in labels:
            labels[root] = Path(root).name or root

    return dict(labels)


def build_desired_state(state: dict, roots: list[str], set_active_all: bool) -> dict:
    desired = dict(state)
    desired["electron-saved-workspace-roots"] = roots
    desired["project-order"] = roots
    desired["active-workspace-roots"] = roots if set_active_all else roots[:1]
    desired["electron-workspace-root-labels"] = build_root_labels(
        roots, state.get("electron-workspace-root-labels")
    )

    persisted = desired.get("electron-persisted-atom-state")
    if not isinstance(persisted, dict):
        persisted = {}
    else:
        persisted = dict(persisted)

    persisted[SIDEBAR_FILTER_KEY] = "all"
    persisted[SIDEBAR_GROUP_KEY] = "project"
    persisted[SIDEBAR_COLLAPSED_KEY] = {}
    desired["electron-persisted-atom-state"] = persisted
    return desired


def repair_sidebar_state(codex_home: Path, set_active_all: bool = True) -> SidebarRepairResult:
    db_path = codex_home / "state_5.sqlite"
    global_state_path = codex_home / ".codex-global-state.json"

    state = load_json(global_state_path)
    roots = ordered_unique(get_roots_from_database(db_path) + get_existing_roots(state))
    if not roots:
        raise RuntimeError("No workspace roots could be reconstructed.")

    desired = build_desired_state(state, roots, set_active_all=set_active_all)
    current_dump = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    desired_dump = json.dumps(desired, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    if current_dump == desired_dump:
        return SidebarRepairResult(
            changed=False,
            backup_path=None,
            root_count=len(roots),
            roots=roots,
            message="侧边栏状态已经是修复后的状态。",
        )

    backup_dir = ensure_backup_dir(codex_home, "sidebar-repair")
    backup_path = backup_file(global_state_path, codex_home, backup_dir)
    save_json(global_state_path, desired)
    return SidebarRepairResult(
        changed=True,
        backup_path=str(backup_path),
        root_count=len(roots),
        roots=roots,
        message="侧边栏状态修复完成。",
    )


def find_rollout_files(codex_home: Path) -> list[Path]:
    results: list[Path] = []
    for folder_name in ("sessions", "archived_sessions"):
        folder = codex_home / folder_name
        if folder.exists():
            results.extend(sorted(folder.rglob("*.jsonl")))
    return results


def find_first_model_provider(obj: Any) -> str | None:
    if isinstance(obj, dict):
        if MODEL_PROVIDER_KEY in obj and isinstance(obj[MODEL_PROVIDER_KEY], str):
            return obj[MODEL_PROVIDER_KEY]
        for value in obj.values():
            found = find_first_model_provider(value)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = find_first_model_provider(item)
            if found:
                return found
    return None


def update_model_provider_values(obj: Any, target_provider: str) -> int:
    changes = 0
    if isinstance(obj, dict):
        for key, value in list(obj.items()):
            if key == MODEL_PROVIDER_KEY and isinstance(value, str) and value != target_provider:
                obj[key] = target_provider
                changes += 1
            else:
                changes += update_model_provider_values(value, target_provider)
    elif isinstance(obj, list):
        for item in obj:
            changes += update_model_provider_values(item, target_provider)
    return changes


def extract_provider_from_rollout(path: Path) -> str | None:
    with path.open("r", encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if index > 10:
                break
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            provider = find_first_model_provider(payload)
            if provider:
                return provider
    return None


def get_rollout_provider_counts(codex_home: Path) -> tuple[dict[str, int], int]:
    counts: dict[str, int] = {}
    rollout_files = find_rollout_files(codex_home)
    for path in rollout_files:
        provider = extract_provider_from_rollout(path) or "(missing)"
        counts[provider] = counts.get(provider, 0) + 1
    return counts, len(rollout_files)


def get_database_provider_counts(db_path: Path) -> dict[str, int]:
    if not db_path.exists():
        return {}
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT model_provider, COUNT(*) FROM threads GROUP BY model_provider ORDER BY model_provider"
        ).fetchall()
    return {provider: count for provider, count in rows}


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8", newline="")


def get_config_provider(config_path: Path) -> str | None:
    if not config_path.exists():
        return None
    text = read_text(config_path)
    match = re.search(r'(?m)^model_provider\s*=\s*"([^"]+)"\s*$', text)
    if match:
        return match.group(1)
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None
    provider = data.get("model_provider")
    return provider if isinstance(provider, str) else None


def set_config_provider(text: str, provider: str) -> str:
    pattern = re.compile(r'(?m)^model_provider\s*=\s*"([^"]+)"\s*$')
    if pattern.search(text):
        return pattern.sub(f'model_provider = "{provider}"', text, count=1)

    insertion = f'model_provider = "{provider}"\n'
    if text.startswith("\ufeff"):
        return "\ufeff" + insertion + text.lstrip("\ufeff")
    return insertion + text


def sync_provider_metadata(
    codex_home: Path,
    target_provider: str | None = None,
    sync_config_provider: bool = False,
) -> ProviderSyncResult:
    db_path = codex_home / "state_5.sqlite"
    config_path = codex_home / "config.toml"

    detected_provider = target_provider or get_config_provider(config_path)
    if not detected_provider:
        raise RuntimeError("无法确定目标 provider，请检查 config.toml 或手动指定。")

    backup_dir: Path | None = None
    sqlite_rows_updated = 0
    rollout_files_updated = 0
    config_updated = False
    scanned_rollouts = 0
    backed_up: set[Path] = set()

    def ensure_file_backup(path: Path) -> None:
        nonlocal backup_dir
        if path in backed_up:
            return
        if backup_dir is None:
            backup_dir = ensure_backup_dir(codex_home, "provider-sync")
        backup_file(path, codex_home, backup_dir)
        backed_up.add(path)

    rollout_files = find_rollout_files(codex_home)
    scanned_rollouts = len(rollout_files)
    for path in rollout_files:
        original_lines = path.read_text(encoding="utf-8").splitlines()
        changed = False
        updated_lines: list[str] = []
        for line in original_lines:
            stripped = line.strip()
            if not stripped:
                updated_lines.append(line)
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                updated_lines.append(line)
                continue
            if update_model_provider_values(obj, detected_provider) > 0:
                changed = True
                updated_lines.append(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
            else:
                updated_lines.append(line)
        if changed:
            ensure_file_backup(path)
            write_text(path, "\n".join(updated_lines) + "\n")
            rollout_files_updated += 1

    with sqlite3.connect(db_path) as conn:
        current_count = conn.execute(
            "SELECT COUNT(*) FROM threads WHERE model_provider != ?",
            (detected_provider,),
        ).fetchone()[0]
        if current_count:
            ensure_file_backup(db_path)
            conn.execute(
                "UPDATE threads SET model_provider = ? WHERE model_provider != ?",
                (detected_provider, detected_provider),
            )
            conn.commit()
            sqlite_rows_updated = int(current_count)

    if sync_config_provider and config_path.exists():
        current_text = read_text(config_path)
        desired_text = set_config_provider(current_text, detected_provider)
        if current_text != desired_text:
            ensure_file_backup(config_path)
            write_text(config_path, desired_text)
            config_updated = True

    changed = rollout_files_updated > 0 or sqlite_rows_updated > 0 or config_updated
    if not changed:
        return ProviderSyncResult(
            changed=False,
            backup_dir=None,
            target_provider=detected_provider,
            scanned_rollouts=scanned_rollouts,
            rollout_files_updated=0,
            sqlite_rows_updated=0,
            config_updated=False,
            message="Provider 元数据已经和当前 provider 一致。",
        )

    return ProviderSyncResult(
        changed=True,
        backup_dir=str(backup_dir) if backup_dir else None,
        target_provider=detected_provider,
        scanned_rollouts=scanned_rollouts,
        rollout_files_updated=rollout_files_updated,
        sqlite_rows_updated=sqlite_rows_updated,
        config_updated=config_updated,
        message="Provider 元数据同步完成。",
    )


def preview(codex_home: Path) -> dict:
    db_path = codex_home / "state_5.sqlite"
    global_state_path = codex_home / ".codex-global-state.json"
    config_path = codex_home / "config.toml"

    state = load_json(global_state_path)
    db_roots = get_roots_from_database(db_path)
    state_roots = get_existing_roots(state)
    merged_roots = ordered_unique(db_roots + state_roots)
    persisted = state.get("electron-persisted-atom-state")
    if not isinstance(persisted, dict):
        persisted = {}

    rollout_provider_counts, rollout_file_count = get_rollout_provider_counts(codex_home)
    db_provider_counts = get_database_provider_counts(db_path)

    return {
        "codex_home": str(codex_home),
        "db_path": str(db_path),
        "global_state_path": str(global_state_path),
        "config_path": str(config_path),
        "db_root_count": len(db_roots),
        "state_root_count": len(state_roots),
        "merged_root_count": len(merged_roots),
        "sidebar_filter": persisted.get(SIDEBAR_FILTER_KEY),
        "sidebar_group": persisted.get(SIDEBAR_GROUP_KEY),
        "config_provider": get_config_provider(config_path),
        "db_provider_counts": db_provider_counts,
        "rollout_provider_counts": rollout_provider_counts,
        "rollout_file_count": rollout_file_count,
        "roots": merged_roots,
    }


def format_provider_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "无"
    return ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))


def format_preview(data: dict) -> str:
    lines = [
        f"Codex 目录: {data['codex_home']}",
        f"当前 config.toml 的 provider: {data['config_provider'] or '(未检测到)'}",
        f"SQLite provider 分布: {format_provider_counts(data['db_provider_counts'])}",
        f"Rollout provider 分布: {format_provider_counts(data['rollout_provider_counts'])}",
        f"Rollout 文件数量: {data['rollout_file_count']}",
        "",
        f"SQLite 中的工作区数量: {data['db_root_count']}",
        f"当前状态文件中的工作区数量: {data['state_root_count']}",
        f"合并后的工作区数量: {data['merged_root_count']}",
        f"侧边栏筛选状态: {data['sidebar_filter']!r}",
        f"侧边栏分组方式: {data['sidebar_group']!r}",
        "",
        "恢复出的工作区列表:",
    ]
    lines.extend(f"  {index}. {root}" for index, root in enumerate(data["roots"], start=1))
    return "\n".join(lines)


def watch_sidebar_state(
    codex_home: Path,
    interval: float,
    set_active_all: bool,
    log: Callable[[str], None],
    stop_event: threading.Event,
) -> None:
    while not stop_event.is_set():
        try:
            result = repair_sidebar_state(codex_home, set_active_all=set_active_all)
            status = "已修正" if result.changed else "已检查"
            log(f"[{datetime.now():%H:%M:%S}] 侧边栏{status}，工作区={result.root_count}")
        except Exception as exc:  # pragma: no cover
            log(f"[{datetime.now():%H:%M:%S}] 守护失败: {exc}")
        stop_event.wait(interval)


class RepairApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Codex 侧边栏对话修复工具")
        self.root.geometry("1080x820")

        self.codex_home_var = tk.StringVar(value=str(default_codex_home()))
        self.active_all_var = tk.BooleanVar(value=True)
        self.interval_var = tk.StringVar(value="5")
        self.sync_provider_var = tk.BooleanVar(value=True)
        self.sync_config_provider_var = tk.BooleanVar(value=False)
        self.target_provider_var = tk.StringVar(value="")

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None

        self._build_ui()
        self.root.after(200, self._drain_logs)
        self.scan()

    def _build_ui(self) -> None:
        frame = ttk.Frame(self.root, padding=12)
        frame.pack(fill="both", expand=True)

        control = ttk.Frame(frame)
        control.pack(fill="x")
        ttk.Label(control, text="Codex 目录").grid(row=0, column=0, sticky="w")
        ttk.Entry(control, textvariable=self.codex_home_var, width=78).grid(
            row=0, column=1, sticky="ew", padx=(8, 8)
        )
        ttk.Button(control, text="扫描", command=self.scan).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(control, text="修复一次", command=self.repair_once).grid(row=0, column=3, padx=(0, 8))
        ttk.Button(control, text="开始守护", command=self.start_watch).grid(row=0, column=4, padx=(0, 8))
        ttk.Button(control, text="停止守护", command=self.stop_watch).grid(row=0, column=5)
        control.columnconfigure(1, weight=1)

        options = ttk.Frame(frame)
        options.pack(fill="x", pady=(10, 8))
        ttk.Checkbutton(
            options,
            text="将 active-workspace-roots 同步为全部恢复出的工作区",
            variable=self.active_all_var,
        ).pack(side="left")
        ttk.Checkbutton(
            options,
            text="修复时同步 provider 元数据",
            variable=self.sync_provider_var,
        ).pack(side="left", padx=(14, 0))
        ttk.Checkbutton(
            options,
            text="同时写回 config.toml 的 provider",
            variable=self.sync_config_provider_var,
        ).pack(side="left", padx=(14, 0))

        provider_row = ttk.Frame(frame)
        provider_row.pack(fill="x", pady=(0, 10))
        ttk.Label(provider_row, text="目标 Provider").pack(side="left")
        ttk.Entry(provider_row, textvariable=self.target_provider_var, width=18).pack(
            side="left", padx=(8, 20)
        )
        ttk.Label(provider_row, text="守护间隔(秒)").pack(side="left")
        ttk.Entry(provider_row, textvariable=self.interval_var, width=8).pack(side="left", padx=(8, 0))

        guide_box = ttk.LabelFrame(frame, text="使用说明", padding=8)
        guide_box.pack(fill="x")
        self.guide_text = tk.Text(guide_box, height=7, wrap="word")
        self.guide_text.pack(fill="both", expand=True)
        self.guide_text.insert(
            "1.0",
            "1. 先点“扫描”，确认能恢复出哪些工作区，以及当前 provider 分布。\n"
            "2. 一般直接点“修复一次”即可，它会同时修复侧边栏状态，并把历史会话同步到当前 provider。\n"
            "3. 如果别人机器上“只看到项目、看不到旧对话”，通常就是 provider 没同步，这一步现在也会自动处理。\n"
            "4. “开始守护”只负责防止 Codex 反复覆盖侧边栏工作区状态；provider 同步通常一次就够。\n"
            "5. 如果你想强制同步到别的 provider，可以手动填写“目标 Provider”，例如 custom 或 openai。\n"
            "6. 下方日志会显示本次扫描、修复和 provider 同步结果。"
        )
        self.guide_text.configure(state="disabled")

        summary_box = ttk.LabelFrame(frame, text="状态概览", padding=8)
        summary_box.pack(fill="x", pady=(10, 0))
        self.summary_text = tk.Text(summary_box, height=13, wrap="word")
        self.summary_text.pack(fill="both", expand=True)

        roots_box = ttk.LabelFrame(frame, text="恢复出的工作区", padding=8)
        roots_box.pack(fill="both", expand=True, pady=(10, 10))
        self.roots_list = tk.Listbox(roots_box)
        self.roots_list.pack(fill="both", expand=True)

        log_box = ttk.LabelFrame(frame, text="日志", padding=8)
        log_box.pack(fill="both", expand=True)
        self.log_text = tk.Text(log_box, height=12, wrap="word")
        self.log_text.pack(fill="both", expand=True)

    def current_codex_home(self) -> Path:
        return Path(self.codex_home_var.get().strip()).expanduser()

    def append_log(self, message: str) -> None:
        self.log_queue.put(message)

    def _drain_logs(self) -> None:
        while True:
            try:
                message = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_text.insert("end", message + "\n")
            self.log_text.see("end")
        self.root.after(200, self._drain_logs)

    def scan(self) -> None:
        try:
            data = preview(self.current_codex_home())
        except Exception as exc:
            self.append_log(f"扫描失败: {exc}")
            if messagebox:
                messagebox.showerror("扫描失败", str(exc))
            return

        if not self.target_provider_var.get().strip() and data["config_provider"]:
            self.target_provider_var.set(data["config_provider"])

        self.summary_text.delete("1.0", "end")
        self.summary_text.insert("1.0", format_preview(data))

        self.roots_list.delete(0, "end")
        for root_path in data["roots"]:
            self.roots_list.insert("end", root_path)

        self.append_log(
            "扫描完成: "
            f"工作区={data['merged_root_count']}，"
            f"config provider={data['config_provider'] or '(未检测到)'}，"
            f"rollout 文件={data['rollout_file_count']}"
        )

    def repair_once(self) -> None:
        try:
            sidebar_result = repair_sidebar_state(
                self.current_codex_home(),
                set_active_all=self.active_all_var.get(),
            )
        except Exception as exc:
            self.append_log(f"侧边栏修复失败: {exc}")
            if messagebox:
                messagebox.showerror("修复失败", str(exc))
            return

        self.append_log(
            f"{sidebar_result.message} 工作区={sidebar_result.root_count}"
            + (f" 备份={sidebar_result.backup_path}" if sidebar_result.backup_path else "")
        )

        if self.sync_provider_var.get():
            target_provider = self.target_provider_var.get().strip() or None
            try:
                provider_result = sync_provider_metadata(
                    self.current_codex_home(),
                    target_provider=target_provider,
                    sync_config_provider=self.sync_config_provider_var.get(),
                )
            except Exception as exc:
                self.append_log(f"Provider 同步失败: {exc}")
                if messagebox:
                    messagebox.showerror("Provider 同步失败", str(exc))
                self.scan()
                return

            self.append_log(
                f"{provider_result.message} 目标={provider_result.target_provider}，"
                f"rollout 更新={provider_result.rollout_files_updated}，"
                f"SQLite 更新={provider_result.sqlite_rows_updated}"
                + (f"，备份目录={provider_result.backup_dir}" if provider_result.backup_dir else "")
            )

        self.scan()
        if messagebox:
            messagebox.showinfo("完成", "修复流程已完成。")

    def start_watch(self) -> None:
        if self.worker and self.worker.is_alive():
            self.append_log("守护已经在运行中。")
            return

        try:
            interval = float(self.interval_var.get().strip())
        except ValueError:
            if messagebox:
                messagebox.showerror("输入错误", "守护间隔必须是数字。")
            return

        self.stop_event = threading.Event()
        self.worker = threading.Thread(
            target=watch_sidebar_state,
            args=(
                self.current_codex_home(),
                interval,
                self.active_all_var.get(),
                self.append_log,
                self.stop_event,
            ),
            daemon=True,
        )
        self.worker.start()
        self.append_log("守护已启动。注意：守护只负责侧边栏状态，不重复扫描全部 rollout。")

    def stop_watch(self) -> None:
        if self.worker and self.worker.is_alive():
            self.stop_event.set()
            self.append_log("已请求停止守护。")
        else:
            self.append_log("当前没有运行中的守护。")


def run_gui() -> int:
    if tk is None or ttk is None:
        print("Tkinter is not available. Please run CLI mode instead.")
        return 1

    root = tk.Tk()
    app = RepairApp(root)
    root.protocol("WM_DELETE_WINDOW", lambda: (app.stop_watch(), root.destroy()))
    root.mainloop()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Repair Codex Desktop sidebar and provider state.")
    parser.add_argument(
        "--codex-home",
        default=str(default_codex_home()),
        help="Codex home directory, default: %(default)s",
    )
    parser.add_argument("--preview", action="store_true", help="Only print the current preview.")
    parser.add_argument("--repair", action="store_true", help="Repair sidebar state once.")
    parser.add_argument("--watch", action="store_true", help="Continuously keep sidebar state repaired.")
    parser.add_argument("--interval", type=float, default=5.0, help="Watch interval in seconds.")
    parser.add_argument(
        "--active-first-only",
        action="store_true",
        help="Keep only the first root in active-workspace-roots.",
    )
    parser.add_argument("--gui", action="store_true", help="Launch the Tkinter GUI.")
    parser.add_argument(
        "--provider",
        help="Target model provider for rollout/SQLite sync. Defaults to config.toml model_provider.",
    )
    parser.add_argument(
        "--skip-provider-sync",
        action="store_true",
        help="Skip provider metadata synchronization when using --repair.",
    )
    parser.add_argument(
        "--sync-config-provider",
        action="store_true",
        help="Also update config.toml root-level model_provider.",
    )
    parser.add_argument(
        "--sync-provider-only",
        action="store_true",
        help="Only sync provider metadata without changing sidebar state.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.gui:
        return run_gui()

    codex_home = Path(args.codex_home).expanduser()
    set_active_all = not args.active_first_only

    if not any((args.preview, args.repair, args.watch, args.sync_provider_only)):
        args.preview = True

    if args.preview:
        print(format_preview(preview(codex_home)))

    if args.sync_provider_only:
        result = sync_provider_metadata(
            codex_home,
            target_provider=args.provider,
            sync_config_provider=args.sync_config_provider,
        )
        print(result.message)
        print(f"Target provider: {result.target_provider}")
        print(f"Rollout files scanned: {result.scanned_rollouts}")
        print(f"Rollout files updated: {result.rollout_files_updated}")
        print(f"SQLite rows updated: {result.sqlite_rows_updated}")
        if result.backup_dir:
            print(f"Backup dir: {result.backup_dir}")

    if args.repair:
        sidebar_result = repair_sidebar_state(codex_home, set_active_all=set_active_all)
        print(sidebar_result.message)
        print(f"Recovered roots: {sidebar_result.root_count}")
        if sidebar_result.backup_path:
            print(f"Sidebar backup: {sidebar_result.backup_path}")

        if not args.skip_provider_sync:
            provider_result = sync_provider_metadata(
                codex_home,
                target_provider=args.provider,
                sync_config_provider=args.sync_config_provider,
            )
            print(provider_result.message)
            print(f"Target provider: {provider_result.target_provider}")
            print(f"Rollout files scanned: {provider_result.scanned_rollouts}")
            print(f"Rollout files updated: {provider_result.rollout_files_updated}")
            print(f"SQLite rows updated: {provider_result.sqlite_rows_updated}")
            if provider_result.backup_dir:
                print(f"Provider backup dir: {provider_result.backup_dir}")

    if args.watch:
        stop_event = threading.Event()

        def log(message: str) -> None:
            print(message, flush=True)

        try:
            watch_sidebar_state(
                codex_home=codex_home,
                interval=args.interval,
                set_active_all=set_active_all,
                log=log,
                stop_event=stop_event,
            )
        except KeyboardInterrupt:
            stop_event.set()
            print("Watcher stopped.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
