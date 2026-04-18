# Codex 侧边栏与对话恢复工具

这是一个用于修复 Codex Desktop 历史对话显示问题的小工具。

它现在不只修复“项目工作区入口”，还会同步历史会话的 `provider` 元数据。这样在别人电脑上使用时，不会再出现“项目文件夹恢复出来了，但旧对话没有显示”的情况。

## 1. 这个工具解决什么问题

适用场景：

- 以前的对话文件还在本地，但 Codex 侧边栏里只显示很少几个项目
- 只能恢复项目分组，看不到项目下面以前的对话
- 旧对话存在于 `sessions` 或 `state_5.sqlite` 中，但因为 `provider` 不一致没有显示出来
- Codex 运行过程中会把本地状态重新覆盖回去

## 2. 为什么以前只能恢复项目，不能恢复对话

之前的版本只修复了“工作区侧边栏状态”，也就是：

- 工作区列表
- 项目排序
- 项目标签
- 侧边栏筛选状态

但真正影响旧对话是否显示出来的，还有一层：

- `sessions/*.jsonl` 与 `archived_sessions/*.jsonl` 中的 `model_provider`
- `state_5.sqlite` 里 `threads.model_provider`

如果这些旧会话的 `provider` 和当前机器上的 Codex 配置不一致，Codex 可能就不会把这些对话显示出来。

这也是为什么有些人用 [codex-provider-sync](https://github.com/Dailin521/codex-provider-sync) 点击“同步”后，对话就出现了。

现在这个工具已经把这一步也补上了。

## 3. 当前版本做了哪些事

修复时会做两类操作。

### A. 修复侧边栏工作区状态

会从 `state_5.sqlite` 中重建工作区根目录，并写回：

- `electron-saved-workspace-roots`
- `project-order`
- `active-workspace-roots`
- `electron-workspace-root-labels`
- `electron-persisted-atom-state`

并强制设置：

- 显示全部工作区
- 按项目分组
- 清空折叠状态

### B. 同步 provider 元数据

会把历史对话同步到当前目标 provider，包括：

- `sessions/**/*.jsonl`
- `archived_sessions/**/*.jsonl`
- `state_5.sqlite` 中的 `threads.model_provider`

可选地还可以同步：

- `config.toml` 根级 `model_provider`

## 4. 当前目录下有哪些文件

- `codex_sidebar_repair.py`
  - 主逻辑脚本
- `codex_sidebar_repair_gui.py`
  - GUI 启动入口
- `build_codex_sidebar_repair_exe.bat`
  - 一键重新打包 EXE
- `launch_codex_sidebar_repair.bat`
  - 直接用 Python 启动 GUI
- `dist\CodexSidebarRepair.exe`
  - 打包好的图形界面程序

## 5. 最简单的使用方式

直接双击运行：

`dist\CodexSidebarRepair.exe`

推荐操作顺序：

1. 点击“扫描”
2. 确认“恢复出的工作区”列表里包含以前用过的项目
3. 查看“当前 config.toml 的 provider”和“Rollout provider 分布”
4. 点击“修复一次”
5. 完全退出 Codex Desktop 再重新打开
6. 查看侧边栏中的旧对话是否已经恢复

## 6. 图形界面说明

### Codex 目录

默认一般是：

`C:\Users\你的用户名\.codex`

通常不需要改。

### 扫描

作用：

- 只读取数据库、配置文件和 rollout 文件
- 不修改任何内容
- 查看当前工作区和 provider 分布

### 修复一次

作用：

- 修复侧边栏工作区状态
- 同步历史对话的 provider 元数据
- 自动备份所有被修改的重要文件

### 开始守护

作用：

- 每隔几秒自动修复一次侧边栏状态
- 用于应对 Codex 运行时把工作区状态覆盖回去

注意：

- 守护模式主要保护侧边栏状态
- `provider` 同步通常执行一次就够，不需要反复扫所有会话

### 停止守护

作用：

- 停止后台自动修复

### 将 active-workspace-roots 同步为全部恢复出的工作区

建议保持勾选。

### 修复时同步 provider 元数据

建议保持勾选。

如果你取消勾选，就只会修复工作区入口，不会同步旧对话的 provider。

### 同时写回 config.toml 的 provider

默认不勾选。

只有在你明确想把 `config.toml` 根级 `model_provider` 一并改掉时再开启。

### 目标 Provider

默认会自动读取当前 `config.toml` 里的 `model_provider`。

例如：

- `custom`
- `openai`

如果你想强制把历史会话同步成某个 provider，可以手动填写这里。

## 7. 命令行用法

### 仅预览

```bash
python codex_sidebar_repair.py --preview
```

### 修复一次

```bash
python codex_sidebar_repair.py --repair
```

### 仅同步 provider

```bash
python codex_sidebar_repair.py --sync-provider-only
```

### 指定目标 provider

```bash
python codex_sidebar_repair.py --repair --provider custom
```

### 同步时顺便修改 config.toml

```bash
python codex_sidebar_repair.py --repair --sync-config-provider
```

### 持续守护侧边栏状态

```bash
python codex_sidebar_repair.py --watch
```

### 打开图形界面

```bash
python codex_sidebar_repair.py --gui
```

## 8. 备份位置

每次真正修改文件前，工具都会自动备份。

备份目录位于：

`C:\Users\你的用户名\.codex\backups_state`

里面会按时间生成不同目录，例如：

- `sidebar-repair-时间戳`
- `provider-sync-时间戳`

## 9. 重新打包 EXE

如果你修改了 Python 文件，重新打包的方法是：

```bat
build_codex_sidebar_repair_exe.bat
```

打包完成后，生成文件在：

`dist\CodexSidebarRepair.exe`

## 10. 注意事项

1. 这个工具修复的是 Codex 本地状态和会话元数据，不会删除聊天内容。
2. 只有本地数据库和 rollout 文件里仍然存在的对话，才有机会被恢复出来。
3. 如果别人电脑上的当前 provider 和旧会话 provider 不一致，必须做 provider 同步，否则可能只看到项目看不到对话。
4. 修复完成后，建议完全退出 Codex，再重新打开，而不是只关闭窗口。
5. 如果侧边栏状态老是被覆盖，可以开“开始守护”。

## 11. 常见问题

### 为什么扫描能看到很多工作区，但还是没有完整对话？

常见原因：

- 只修了工作区，没有同步 provider
- Codex 运行时又把状态覆盖了
- 没有完全退出并重新打开 Codex

### 为什么现在和 codex-provider-sync 的效果接近了？

因为现在这个工具也加入了对 `model_provider` 的同步，不再只修工作区列表。

### provider 同步安全吗？

风险比较低，原因是：

- 工具只修改 Codex 本地文件
- 每次修改前都会自动备份
- 不会删除原始会话内容

## 12. 推荐使用顺序

1. 双击 `dist\CodexSidebarRepair.exe`
2. 点击“扫描”
3. 保持“修复时同步 provider 元数据”为勾选状态
4. 点击“修复一次”
5. 完全退出 Codex
6. 重新打开 Codex
7. 如果工作区状态仍然会被改回去，再点击“开始守护”

