#!/usr/bin/env python3
"""
Codex sidebar repair tool.

What it does:
1. Reads Codex thread history from state_5.sqlite.
2. Reconstructs workspace roots from unarchived threads.
3. Normalizes Windows roots and removes duplicate entries.
4. Repairs .codex-global-state.json so the sidebar can show all projects.
5. Forces sidebar UI state to show all workspaces grouped by project.
6. Provides both CLI mode and a small Tkinter GUI.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import shutil
import sqlite3
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

try:
    import tkinter as tk
    from tkinter import messagebox
    from tkinter import ttk
except Exception:  # pragma: no cover - CLI mode still works without Tkinter
    tk = None
    ttk = None
    messagebox = None


SIDEBAR_FILTER_KEY = "sidebar-workspace-filter-v2"
SIDEBAR_GROUP_KEY = "sidebar-organize-mode-v1"
SIDEBAR_COLLAPSED_KEY = "sidebar-collapsed-groups"


@dataclass
class RepairResult:
    changed: bool
    backup_path: str | None
    root_count: int
    roots: list[str]
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


def make_backup(source: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"global-state-backup-{timestamp}.json"
    shutil.copy2(source, backup_path)
    return backup_path


def preview(codex_home: Path) -> dict:
    db_path = codex_home / "state_5.sqlite"
    global_state_path = codex_home / ".codex-global-state.json"

    state = load_json(global_state_path)
    db_roots = get_roots_from_database(db_path)
    state_roots = get_existing_roots(state)
    merged_roots = ordered_unique(db_roots + state_roots)

    persisted = state.get("electron-persisted-atom-state")
    if not isinstance(persisted, dict):
        persisted = {}

    return {
        "codex_home": str(codex_home),
        "db_path": str(db_path),
        "global_state_path": str(global_state_path),
        "db_root_count": len(db_roots),
        "state_root_count": len(state_roots),
        "merged_root_count": len(merged_roots),
        "sidebar_filter": persisted.get(SIDEBAR_FILTER_KEY),
        "sidebar_group": persisted.get(SIDEBAR_GROUP_KEY),
        "roots": merged_roots,
    }


def repair(codex_home: Path, set_active_all: bool = True) -> RepairResult:
    db_path = codex_home / "state_5.sqlite"
    global_state_path = codex_home / ".codex-global-state.json"
    backup_dir = codex_home / "backups_state"

    state = load_json(global_state_path)
    roots = ordered_unique(get_roots_from_database(db_path) + get_existing_roots(state))
    if not roots:
        raise RuntimeError("No workspace roots could be reconstructed.")

    desired = build_desired_state(state, roots, set_active_all=set_active_all)
    current_dump = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    desired_dump = json.dumps(desired, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    if current_dump == desired_dump:
        return RepairResult(
            changed=False,
            backup_path=None,
            root_count=len(roots),
            roots=roots,
            message="State is already in the repaired form.",
        )

    backup_path = make_backup(global_state_path, backup_dir)
    save_json(global_state_path, desired)
    return RepairResult(
        changed=True,
        backup_path=str(backup_path),
        root_count=len(roots),
        roots=roots,
        message="Repair completed successfully.",
    )


def watch(
    codex_home: Path,
    interval: float,
    set_active_all: bool,
    log: Callable[[str], None],
    stop_event: threading.Event,
) -> None:
    while not stop_event.is_set():
        try:
            result = repair(codex_home, set_active_all=set_active_all)
            status = "updated" if result.changed else "checked"
            log(f"[{datetime.now():%H:%M:%S}] {status}: {result.root_count} roots")
        except Exception as exc:  # pragma: no cover - UI runtime safeguard
            log(f"[{datetime.now():%H:%M:%S}] error: {exc}")
        stop_event.wait(interval)


def format_preview(data: dict) -> str:
    lines = [
        f"Codex 目录: {data['codex_home']}",
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


class RepairApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Codex 侧边栏对话修复工具")
        self.root.geometry("980x700")

        self.codex_home_var = tk.StringVar(value=str(default_codex_home()))
        self.active_all_var = tk.BooleanVar(value=True)
        self.interval_var = tk.StringVar(value="5")

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
        ttk.Entry(control, textvariable=self.codex_home_var, width=80).grid(
            row=0, column=1, sticky="ew", padx=(8, 8)
        )
        ttk.Button(control, text="扫描", command=self.scan).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(control, text="修复一次", command=self.repair_once).grid(row=0, column=3, padx=(0, 8))
        ttk.Button(control, text="开始守护", command=self.start_watch).grid(row=0, column=4, padx=(0, 8))
        ttk.Button(control, text="停止守护", command=self.stop_watch).grid(row=0, column=5)
        control.columnconfigure(1, weight=1)

        options = ttk.Frame(frame)
        options.pack(fill="x", pady=(10, 10))
        ttk.Checkbutton(
            options,
            text="将 active-workspace-roots 同步为全部恢复出的工作区",
            variable=self.active_all_var,
        ).pack(side="left")
        ttk.Label(options, text="守护间隔(秒)").pack(side="left", padx=(16, 8))
        ttk.Entry(options, textvariable=self.interval_var, width=8).pack(side="left")

        guide_box = ttk.LabelFrame(frame, text="使用说明", padding=8)
        guide_box.pack(fill="x")
        self.guide_text = tk.Text(guide_box, height=5, wrap="word")
        self.guide_text.pack(fill="both", expand=True)
        self.guide_text.insert(
            "1.0",
            "1. 先点“扫描”，检查能恢复出多少工作区。\n"
            "2. 一般直接点“修复一次”即可，它会先自动备份，再修改侧边栏状态。\n"
            "3. 如果 Codex 运行时又把状态改回去了，就点“开始守护”，工具会每隔几秒自动修正一次。\n"
            "4. 修完后重新打开 Codex，侧边栏通常就会显示更多旧对话。\n"
            "5. 下方“日志”会显示本次扫描/修复结果。"
        )
        self.guide_text.configure(state="disabled")

        summary_box = ttk.LabelFrame(frame, text="状态概览", padding=8)
        summary_box.pack(fill="x")
        self.summary_text = tk.Text(summary_box, height=10, wrap="word")
        self.summary_text.pack(fill="both", expand=True)

        roots_box = ttk.LabelFrame(frame, text="恢复出的工作区", padding=8)
        roots_box.pack(fill="both", expand=True, pady=(10, 10))
        self.roots_list = tk.Listbox(roots_box)
        self.roots_list.pack(fill="both", expand=True)

        log_box = ttk.LabelFrame(frame, text="日志", padding=8)
        log_box.pack(fill="both", expand=True)
        self.log_text = tk.Text(log_box, height=10, wrap="word")
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

        self.summary_text.delete("1.0", "end")
        self.summary_text.insert("1.0", format_preview(data))

        self.roots_list.delete(0, "end")
        for root_path in data["roots"]:
            self.roots_list.insert("end", root_path)

        self.append_log(
            f"扫描完成: SQLite={data['db_root_count']}, 合并后={data['merged_root_count']}"
        )

    def repair_once(self) -> None:
        try:
            result = repair(self.current_codex_home(), set_active_all=self.active_all_var.get())
        except Exception as exc:
            self.append_log(f"修复失败: {exc}")
            if messagebox:
                messagebox.showerror("修复失败", str(exc))
            return

        self.append_log(
            f"{result.message} roots={result.root_count}"
            + (f" backup={result.backup_path}" if result.backup_path else "")
        )
        self.scan()
        if messagebox:
            messagebox.showinfo("完成", "修复完成。")

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
            target=watch,
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
        self.append_log("守护已启动。")

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
    parser = argparse.ArgumentParser(description="Repair Codex Desktop sidebar workspace state.")
    parser.add_argument(
        "--codex-home",
        default=str(default_codex_home()),
        help="Codex home directory, default: %(default)s",
    )
    parser.add_argument("--preview", action="store_true", help="Only print the current preview.")
    parser.add_argument("--repair", action="store_true", help="Repair the global state once.")
    parser.add_argument("--watch", action="store_true", help="Continuously keep the state repaired.")
    parser.add_argument("--interval", type=float, default=5.0, help="Watch interval in seconds.")
    parser.add_argument(
        "--active-first-only",
        action="store_true",
        help="Keep only the first root in active-workspace-roots.",
    )
    parser.add_argument("--gui", action="store_true", help="Launch the Tkinter GUI.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.gui:
        return run_gui()

    codex_home = Path(args.codex_home).expanduser()
    set_active_all = not args.active_first_only

    if not any((args.preview, args.repair, args.watch)):
        args.preview = True

    if args.preview:
        print(format_preview(preview(codex_home)))

    if args.repair:
        result = repair(codex_home, set_active_all=set_active_all)
        print(result.message)
        print(f"Recovered roots: {result.root_count}")
        if result.backup_path:
            print(f"Backup: {result.backup_path}")

    if args.watch:
        stop_event = threading.Event()

        def log(message: str) -> None:
            print(message, flush=True)

        try:
            watch(
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
