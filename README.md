# Codex 侧边栏对话修复工具

这是一个用于修复 Codex Desktop 侧边栏历史对话显示问题的小工具。

适用场景：

- 以前的很多对话还在本地数据库里，但侧边栏只显示很少几个项目
- 侧边栏分组不完整，只能看到少量工作区
- 修改过状态文件后，Codex 运行时又把设置覆盖回去

这个工具已经封装成了图形界面程序，也可以用 Python 命令行方式运行。

## 1. 工具原理

这个工具的修复逻辑来自本次实际排查结果，核心做了下面几件事：

1. 从 `state_5.sqlite` 中读取未归档线程的 `cwd`
2. 重建历史工作区根目录列表
3. 去掉 Windows 路径中的 `\\?\` 前缀
4. 对工作区路径按顺序去重
5. 回填 `.codex-global-state.json` 中与侧边栏相关的状态
6. 强制将侧边栏设置为：
   - 显示全部工作区
   - 按项目分组
   - 清空折叠分组状态
7. 修复前自动备份状态文件
8. 可选开启“守护模式”，定时重复修复，防止运行中的 Codex 再次覆盖状态

## 2. 相关文件

当前目录下主要有这些文件：

- `codex_sidebar_repair.py`
  - 主逻辑文件
- `codex_sidebar_repair_gui.py`
  - GUI 入口
- `build_codex_sidebar_repair_exe.bat`
  - 重新打包 EXE 的脚本
- `launch_codex_sidebar_repair.bat`
  - 直接用 Python 启动 GUI 的脚本
- `dist\CodexSidebarRepair.exe`
  - 打包好的可执行文件

## 3. 最简单的使用方式

直接双击运行：

`dist\CodexSidebarRepair.exe`

推荐操作顺序：

1. 点击“扫描”
2. 查看“恢复出的工作区”里是否包含你以前用过的项目
3. 点击“修复一次”
4. 关闭并重新打开 Codex Desktop
5. 检查侧边栏中的旧对话是否已经恢复

如果你发现 Codex 打开后又把状态改回去了：

1. 打开工具
2. 点击“开始守护”
3. 保持工具运行
4. 再重新打开 Codex

不用守护时，点击“停止守护”即可。

## 4. 图形界面说明

### Codex 目录

默认一般是：

`C:\Users\你的用户名\.codex`

如果你的 Codex 数据目录没有改过，保持默认即可。

### 扫描

作用：

- 只读取数据库和状态文件
- 不修改任何内容
- 用于确认能恢复出多少工作区

### 修复一次

作用：

- 立即执行一次修复
- 自动备份当前 `.codex-global-state.json`
- 写入修复后的工作区和侧边栏状态

### 开始守护

作用：

- 每隔几秒自动执行一次检查和修复
- 用于应对 Codex 运行时把状态覆盖回去的情况

### 停止守护

作用：

- 停止后台自动修复

### 将 active-workspace-roots 同步为全部恢复出的工作区

勾选时：

- 会把 `active-workspace-roots` 设置为全部恢复出的工作区

一般建议保持勾选。

## 5. 命令行用法

如果你不想用图形界面，也可以直接运行 Python 脚本。

### 仅预览

```bash
python codex_sidebar_repair.py --preview
```

### 修复一次

```bash
python codex_sidebar_repair.py --repair
```

### 持续守护

```bash
python codex_sidebar_repair.py --watch
```

### 打开图形界面

```bash
python codex_sidebar_repair.py --gui
```

### 指定 Codex 数据目录

```bash
python codex_sidebar_repair.py --repair --codex-home "C:\Users\SYKQW\.codex"
```

## 6. 备份位置

每次真正执行“修复一次”时，工具会先备份：

`.codex-global-state.json`

备份目录为：

`C:\Users\你的用户名\.codex\backups_state`

如果修复结果不满意，你可以从这里手动找回旧版本。

## 7. 重新打包 EXE

如果你后面修改了 Python 代码，想重新生成 EXE，可以运行：

```bat
build_codex_sidebar_repair_exe.bat
```

生成后的文件在：

`dist\CodexSidebarRepair.exe`

## 8. 注意事项

1. 这个工具主要修复“侧边栏不显示完整历史对话”的问题，不会修改聊天内容本身。
2. 只有数据库里仍然存在的对话，才有机会通过这个工具重新显示出来。
3. 如果某些项目根目录已经不存在，仍然有可能恢复出分组，但显示效果可能受 Codex 当前版本影响。
4. 如果 Codex 正在运行，它可能会把状态再次改回去，所以有时需要配合“守护模式”。
5. 修复完成后，通常建议完全退出 Codex 再重新打开，而不是只关闭窗口。

## 9. 常见问题

### 为什么扫描出来很多工作区，但侧边栏还是没全出来？

可能原因：

- Codex 还在运行，并再次覆盖了状态
- 当前版本 Codex 对某些工作区还有额外筛选
- 需要彻底退出 Codex 后重新进入

建议：

1. 先点“修复一次”
2. 完全退出 Codex
3. 必要时开启“开始守护”
4. 再重新打开 Codex

### 为什么工具里能看到工作区，但 Codex 里没有对应对话？

这说明：

- 工作区路径可能还在数据库里
- 但具体线程是否还能显示，还受 Codex 当前前端筛选逻辑影响

### 修复会不会有风险？

风险很低，原因是：

- 工具只改 Codex 的本地状态文件
- 每次修复前会自动备份
- 不会删除数据库内容

## 10. 推荐用法

对大多数人来说，最推荐的方式就是：

1. 双击 `dist\CodexSidebarRepair.exe`
2. 点“扫描”
3. 点“修复一次”
4. 重新打开 Codex
5. 如果状态又被覆盖，再点“开始守护”

