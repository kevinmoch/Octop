# RUN.md — 启动、构建与访问方式

本文档适用于当前仓库（`D:\Github\Octop`）。包含：

1. 启动 Web 服务（含局域网访问）
2. 启动 Windows 桌面应用
3. 构建 Windows 安装包
4. 两种形态同时运行时的注意事项

---

## 0. 环境准备（首次）

在仓库根目录执行：

```powershell
cd D:\Github\Octop
uv sync
```

Windows 桌面壳需要 Node.js ≥ 22.6 与 pnpm 12（`desktop/src/package.json` 的 `engines` 要求）：

```powershell
cd D:\Github\Octop\desktop\src
pnpm install
```

---

## 1. 启动 Web 服务

> 1.1–1.4 是**源码 checkout** 的启动方式。用户机器不需要拷贝 GitHub 仓库，可以用安装包自带的 Python runtime 或独立的 Octop 包，见 1.5。

### 1.1 仅本机访问

```powershell
cd D:\Github\Octop
uv run octop run
```

也可以直接使用仓库虚拟环境里的 CLI：

```powershell
D:\Github\Octop\.venv\Scripts\octop.exe run
```

默认地址：<http://127.0.0.1:8088>

首次启动如果还没有管理员，按页面提示完成初始化（或在终端使用 `octop init` / 设置向导）。

### 1.2 允许局域网内其他电脑访问

```powershell
cd D:\Github\Octop
uv run octop run --host 0.0.0.0 --port 8088
```

说明：

- `--host 0.0.0.0` 会监听所有网卡；`--host` / `--port` 会被写入 `~/.octop/config.json`，下次直接 `octop run` 也会沿用。
- 也可以不传参数，改用环境变量：
  ```powershell
  $env:OCTOP_BIND_HOST = "0.0.0.0"
  $env:OCTOP_PORT = "8088"
  uv run octop run
  ```
- 需要放行 Windows 防火墙（管理员 PowerShell，只需一次）：
  ```powershell
  New-NetFirewallRule -DisplayName "Octop Web 8088" -Direction Inbound -Protocol TCP -LocalPort 8088 -Action Allow
  ```
- 其他电脑访问 `http://<这台机器的局域网IP>:8088`；本机 IP 可用 `ipconfig` 查看。
- 局域网用户需要 Octop 账号；不要用管理员账号共享给所有人。

### 1.3 停止 Web 服务

前台运行时按 `Ctrl+C`。

如果是在后台启动的，按端口结束进程：

```powershell
Get-NetTCPConnection -LocalPort 8088 -State Listen |
  Select-Object -ExpandProperty OwningProcess |
  ForEach-Object { Stop-Process -Id $_ -Force }
```

### 1.4 开机自启（可选）

`octop service` 目前只支持 Linux systemd / macOS launchd，Windows 下不支持。Windows 需要开机自启时，可用「任务计划程序」在登录时运行：

```powershell
D:\Github\Octop\.venv\Scripts\octop.exe run --host 0.0.0.0 --port 8088
```

或使用 NSSM 等第三方工具包装成 Windows 服务。

---

### 1.5 不拷贝仓库：用已安装应用/独立包启动 Web 服务

**方式 A：使用 Windows 安装包自带的 Python 运行时（推荐，版本与桌面版一致）**

安装版会把绿色运行时解压到安装目录的 `resources\sidecar`。先找到安装目录：

```powershell
$roots = @("$env:LOCALAPPDATA\Programs", $env:ProgramFiles, ${env:ProgramFiles(x86)}) |
  Where-Object { $_ -and (Test-Path $_) }
$exe = Get-ChildItem $roots -Filter "CCS Buddy.exe" -Recurse -ErrorAction SilentlyContinue |
  Select-Object -First 1
if (-not $exe) { throw "找不到 CCS Buddy.exe，请确认安装路径" }
$sidecar = Join-Path $exe.Directory.FullName "resources\sidecar"
Test-Path (Join-Path $sidecar "launch.py")   # 应为 True
```

然后启动普通 Web 服务（不需要 Git 仓库、不需要单独安装 Python）：

```powershell
Push-Location $sidecar
& ".\runtime\python.exe" ".\launch.py" run --host 0.0.0.0 --port 8088
Pop-Location
```

- 默认使用 `%USERPROFILE%\.octop`，与桌面应用共用数据库/配置；
- 这是普通 Web 服务模式，其他电脑用 Octop 账号登录；
- 防火墙需放行 8088（见 1.2）；
- 该窗口保持打开即服务运行；关闭窗口或 `Ctrl+C` 停止。

如果只有 `win-unpacked`（`resources\sidecar` 不存在），把 `resources\portable\Octop-portable-*.zip` 解压到任意目录，找到其中的 `launch.py`，然后用同一目录下的 `runtime\python.exe` 执行：

```powershell
& ".\runtime\python.exe" ".\launch.py" run --host 0.0.0.0 --port 8088
```

**方式 B：安装官方 Octop Python 包**

```powershell
pip install octop
octop run --host 0.0.0.0 --port 8088
```

或用 `uv tool install octop`（命令同样是 `octop run`）。这不需要源码仓库，但安装的是 PyPI 版本，不一定包含本仓库的定制改动。

**方式 C：Docker**

按官方 README 的 Docker 方式运行，把容器 `8088` 映射到宿主机并挂载数据目录即可；Windows 桌面应用不复用 Docker 容器内的数据目录，需要单独初始化。

> 当前 Windows 安装包**只启动桌面应用**，不会自动启动这个 Web 服务；需要在安装后手动执行上述命令（或自行注册 Windows 任务计划/NSSM 服务）。

---

## 2. 启动 Windows 桌面应用

### 2.1 开发模式（推荐用于调试）

在 **PowerShell** 中执行（不要用 Git Bash；`pnpm dev` 走的是 Unix 的 `run-dev.sh`，Windows 上会报 `sed: command not found`）：

```powershell
cd D:\Github\Octop\desktop\src
.\scripts\dev-win.cmd
```

**运行前先退出已经打开的 CCS Buddy**（系统托盘 → 退出）。Windows 桌面版和 dev 模式共用单实例锁与 `.dev-runtime`，同时运行会出现 `EBUSY`、`Unable to move the cache`，或者 dev 窗口一闪就退出。

或者直接使用 Node（不需要 pnpm 在 PATH 里）：

```powershell
cd D:\Github\Octop\desktop\src
node scripts\run-dev.mjs
```

启动脚本会：

1. 准备 `.dev-runtime`（复用仓库根目录的 `.venv`）；
2. 用本地 `electron-vite` 构建 Electron 主进程/预加载脚本；
3. 启动 Dashboard 的 Vite 开发服务器（默认 5173）并打开 Electron 窗口。

停止：在终端按 `Ctrl+C`，会同时结束 Electron 和它启动的 Python sidecar。

> 如果 `pnpm` 已经在 PATH 中，`pnpm dev:win` 也可以，但它只负责执行
> `node scripts/run-dev.mjs`；报 `bash: pnpm: command not found` 说明当前终端
> （通常是 Git Bash）找不到 Windows 的 pnpm，不是脚本本身的问题。
>
> 不要使用 `pnpm dev`：它调用 `bash scripts/run-dev.sh`，Windows 没有 `sed` 等
> Unix 工具时会直接失败。

### 2.2 已构建/已安装的桌面应用

构建后的免安装目录：

```text
D:\Github\Octop\desktop\src\release\win-unpacked\CCS Buddy.exe
```

已安装版本：开始菜单搜索 **CCS Buddy** / **基建个人智能助理** 打开即可。

桌面应用会自己启动一个 Python sidecar（随机端口、仅 `127.0.0.1`），**不需要**先启动 Web 服务；它和 Web 服务是两套入口，但共用 `%USERPROFILE%\.octop` 数据目录。

---

## 3. 构建 Windows 安装包

### 3.1 前置资源

`构建安装包` 需要已准备好的便携 Python 运行时（绿色包）：

```text
desktop\src\resources\portable\Octop-portable-windows-amd64-<version>.zip
```

当前仓库里已经有该 zip；如果缺失，可在 Git Bash / WSL 中重新生成：

```bash
make -f desktop/portable/Makefile green
```

或使用 CI 工作流 `.github/workflows/octop-desktop.yml` 产出的绿色包。

### 3.2 构建

在 PowerShell 中执行（只需要 Node，不需要 pnpm/bash/sed）：

```powershell
# 重建 portable.zip
& "C:\Program Files\Git\bin\bash.exe" -lc "cd /d/Github/Octop && bash desktop/portable/package.sh windows-amd64"
& "C:\Program Files\Git\bin\bash.exe" -lc "cd /d/Github/Octop && bash desktop/portable/stage-electron-runtime.sh windows-amd64 1.0.3b6"

cd D:\Github\Octop\desktop\src
.\scripts\build-win.cmd          # 只构建 Electron 代码
.\scripts\package-win.cmd --dir  # 生成免安装目录：release\win-unpacked\
.\scripts\package-win.cmd        # 生成 NSIS 安装包：release\CCS-Buddy-desktop-windows-amd64-<version>.exe
```

对应的直接命令（效果相同）：

```powershell
node node_modules\electron-vite\bin\electron-vite.js build
node node_modules\electron-builder\cli.js --config electron-builder.config.ts --dir
node node_modules\electron-builder\cli.js --config electron-builder.config.ts
```

打包脚本现在会：

1. 先执行 `npm --prefix ../../dashboard run build`，把最新前端构建到 `src/octop/dashboard`；
2. 通过 `extraResources` 把它复制到 `resources\dashboard`，应用启动时优先加载这份；
3. 打包后运行 `scripts\verify-package.mjs`，检查 `resources\dashboard`、WebSkill host 和四个内置技能是否都在产物里。

**注意**：Python sidecar 仍然来自 `resources\portable\Octop-portable-*.zip`。如果本次改动包含 Python 后端代码，必须重新生成这个绿色包并提升版本号，否则 Windows 会复用 `%USERPROFILE%\.octop\portable` 里的旧 runtime（前端 Dashboard 不受此影响）。

`pnpm build` / `pnpm package` 仍然可用（`package.json` 已改为直接调用 Node，不再经过 `.bin` shell shim），但前提是 **pnpm 本身在 PATH 中**；Git Bash 里通常会因为缺少 pnpm 或 `sed` 而失败，因此 Windows 上推荐上面的脚本。

> **不会再下载 `electron-v45.0.0-win32-x64.zip`**：`electron-builder.config.ts` 在 Windows 上会把 `electronDist` 指向已安装的 `node_modules/electron/dist`（`pnpm install` 时已存在），electron-builder 直接复制本地 Electron，不再从 GitHub 下载整包。只有删除 `node_modules/electron/dist` 或在非 Windows 主机上打包时才会回退到下载。

安装包文件名里的版本取自 `package.json` 的 `version` 或 `OCTOP_VERSION` 环境变量。需要与 Python 版本一致时：

```powershell
$env:OCTOP_VERSION = "1.0.3b6"
.\scripts\package-win.cmd
```

品牌构建的 bootstrap 配置（默认浏览器首页等）会自动使用：

```text
desktop\src\resources\bootstrap\config.json
```

前提是同目录存在 `users.json` 与 `models.json`（两者已 gitignore）。也可以显式覆盖：

```powershell
$env:OCTOP_DESKTOP_BOOTSTRAP_CONFIG = "D:\Github\Octop\desktop\src\resources\bootstrap\config.json"
pnpm package
```

### 3.3 构建产物位置

```text
desktop\src\release\
  CCS-Buddy-desktop-windows-amd64-<version>.exe   # 安装包
  win-unpacked\CCS Buddy.exe                      # 免安装运行
```

---

## 4. 本地应用 + 对外 Web 端口同时使用

当前 Windows 安装版**默认只提供桌面应用**：内置 sidecar 固定绑定 `127.0.0.1` 的随机端口，没有安装 Windows 服务，也没有对外监听，所以装完后其他电脑无法通过浏览器访问。

如果既要本地桌面应用，又要其他网络用户通过浏览器访问，需要在同一台机器上再启动一个 Web 服务：

```powershell
cd D:\Github\Octop
uv run octop run --host 0.0.0.0 --port 8088
```

然后正常打开桌面应用即可。两者共用 `~/.octop`（同一个数据库/配置），注意事项：

- 桌面应用和 Web 服务不要同时运行相互冲突的自动化任务；
- 不要把桌面 sidecar 的随机端口暴露到局域网（它是桌面设备凭证模式，不是给其他用户用的 Web 入口）；
- Web 服务对外用 `8088`，桌面应用自己的 sidecar 端口只在本机随机分配。

---

## 5. 快速检查

```powershell
# Web 是否启动
Invoke-WebRequest http://127.0.0.1:8088/api/setup/status -UseBasicParsing

# 8088 是否被占用
Get-NetTCPConnection -LocalPort 8088 -State Listen

# 桌面应用日志
Get-Content "$env:USERPROFILE\.octop\logs\desktop\desktop.log" -Tail 20
```

局域网访问不通时，依次检查：`--host 0.0.0.0` 是否生效、Windows 防火墙是否放行 8088、两台机器是否在同一网段、访问地址是否用了本机局域网 IP（不是 `127.0.0.1`）。

---

## 6. 常见问题

### `bash: sed: command not found` / `bash: pnpm: command not found`

这两个错误都来自**在 Windows 上用 Git Bash 执行了 Unix 脚本或 pnpm 的 shell shim**：

- `pnpm dev` → `bash scripts/run-dev.sh`（依赖 `sed` 等 Unix 工具）；
- `pnpm build` / `pnpm package` 若由 bash 执行，会走 `node_modules/.bin` 的 POSIX shim，同样可能调用 `sed`；
- Git Bash 的 PATH 里没有 Windows 的 pnpm（`C:\Users\<用户>\AppData\Roaming\npm`）。

正确做法（PowerShell，不需要 pnpm/sed）：

```powershell
cd D:\Github\Octop\desktop\src
.\scripts\dev-win.cmd          # 开发
.\scripts\build-win.cmd        # 只构建
.\scripts\package-win.cmd      # 构建并打包
```

或直接：

```powershell
node scripts\run-dev.mjs
node node_modules\electron-vite\bin\electron-vite.js build
node node_modules\electron-builder\cli.js --config electron-builder.config.ts
```

不需要 `pnpm`，也不需要 `sed`。

### 打包时下载 `electron-v45.0.0-win32-x64.zip` 很慢

electron-builder 默认只认自己的 **zip 缓存**，即使 `node_modules/electron/dist` 已经存在也会去 GitHub 下载对应版本。本仓库的 `electron-builder.config.ts` 已在 Windows 下配置：

```ts
electronDist: "node_modules/electron/dist"
```

因此正常打包会打印 `using custom unpacked Electron distribution`，直接复制本地 Electron，不需要下载。若仍然在下载：

1. 确认 `desktop/src/node_modules/electron/dist/electron.exe` 存在；
2. 确认当前是 **Windows** 主机（该优化只在 `win32` 下启用）；
3. 删除 `release/` 后重试；
4. `pnpm install` 重新安装 Electron 依赖。

### `EBUSY`、`Unable to move the cache` 或窗口一闪就退出

说明已经有一个 CCS Buddy / dev 实例在运行：

1. 在系统托盘右键 CCS Buddy → 退出（或任务管理器结束 `CCS Buddy.exe`）；
2. 确认没有残留的 `electron.exe`；
3. 再执行 `.\scripts\dev-win.cmd`。

`.dev-runtime` 里的 `python.exe` 被 sidecar 占用时，脚本会复用已有副本；但 Electron 的单实例锁仍要求先退出旧窗口。

### 仍然想用 `pnpm dev:win` / `pnpm build`

项目接受 pnpm **12.x**（CI 固定 12.15.0，其他 12.x 也可）。确保在 **PowerShell**（或 cmd）中执行，并确认：

```powershell
Get-Command pnpm
pnpm --version
```

如果 PowerShell 能找到 pnpm，`pnpm dev:win` 与 `node scripts\run-dev.mjs` 等价，`pnpm build` 会直接调用 Node 版的 `electron-vite`，不再经过 `.bin` shell shim。

如果报 `ERR_PNPM_BAD_PM_VERSION`（pnpm 不在 12.x），可以：

```powershell
pnpm --pm-on-fail=ignore build
```

或直接使用 `.\scripts\build-win.cmd`。不要用 Git Bash 执行 `pnpm dev` / `pnpm build`；Git Bash 的 PATH 通常既没有 Windows 的 pnpm，也没有 `sed`。

### `pnpm package` 提示缺少 portable zip

把绿色运行时 zip 放到 `desktop/src/resources/portable/`，或在 Git Bash / WSL 里运行：

```bash
make -f desktop/portable/Makefile green
```
