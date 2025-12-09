# AhuQiangKe

一键运行脚本，自动完成 Python 环境检测/静默安装、pip 镜像配置、依赖安装并启动 `course_selector.py`。

## 快速开始（Windows）

方式 A：双击/命令行运行 `run.cmd`（自动使用 `pwsh` 或 `powershell`，并使用 ExecutionPolicy Bypass）。

方式 B：手动 PowerShell：
```pwsh
cd d:\Coding\AhuQiangKe
pwsh ./run.ps1
```

## 脚本做了什么
- 检测本机 `python`，若不存在则从清华镜像选择最新可用版本（按架构优先 amd64）静默安装。
- 自动写入 `%APPDATA%\pip\pip.ini`，使用清华镜像源。
- 通过 `python -m pip` 安装 `requirements.txt` 中的依赖。
- 使用检测/安装到的 Python 启动 `course_selector.py`。

## 依赖
`requirements.txt`：
- requests
- ntplib

## 注意
- 需要联网访问 `https://mirrors.tuna.tsinghua.edu.cn/python/` 以及 `https://pypi.tuna.tsinghua.edu.cn/simple`。
- 脚本使用静默安装参数 `/quiet InstallAllUsers=1 PrependPath=1 Include_pip=1 Include_test=0`。
- 若已有 Python，脚本不会重新安装，会直接复用现有版本。
