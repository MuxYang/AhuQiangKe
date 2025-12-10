#requires -Version 5.1
param()

$ErrorActionPreference = "Stop"

function Write-Tag {
    param(
        [string]$Label,
        [string]$Message
    )
    Write-Host "[$Label] $Message"
}
function Info($m) { Write-Tag "INFO" $m }
function Warn($m) { Write-Tag "WARN" $m }
function Ok($m)   { Write-Tag " OK " $m }
function Fail($m) { Write-Tag "FAIL" $m }

function Test-Administrator {
    $currentUser = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($currentUser)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Request-AdminPrivilege {
    if (-not (Test-Administrator)) {
        Info "检测到需要管理员权限，正在请求提升..."
        $scriptPath = $MyInvocation.ScriptName
        $arguments = "-NoLogo -NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`""
        try {
            Start-Process -FilePath "powershell.exe" -ArgumentList $arguments -Verb RunAs
            exit
        } catch {
            Fail "无法获取管理员权限: $_"
            throw "需要管理员权限才能为所有用户安装 Python"
        }
    }
}

function Get-PythonPath {
    try {
        $cmd = Get-Command python -ErrorAction Stop
        return $cmd.Source
    } catch {
        return $null
    }
}

function Get-LatestPythonInstaller {
    $mirrorRoot = "https://mirrors.tuna.tsinghua.edu.cn/python/"
    Info "查询清华镜像可用版本..."
    $resp = Invoke-WebRequest -Uri $mirrorRoot -UseBasicParsing
    $versions = $resp.Links
        | Where-Object { $_.href -match '^\d+\.\d+\.\d+/$' }
        | ForEach-Object { $_.href.TrimEnd('/') }
        | Sort-Object { [version]$_ } -Descending
    if (-not $versions) { throw "未获取到版本列表" }

    $is64 = [Environment]::Is64BitOperatingSystem
    $candidatesForArch = if ($is64) { @("amd64", "") } else { @("", "amd64") }

    foreach ($ver in $versions) {
        $base = "$mirrorRoot$ver/"
        foreach ($arch in $candidatesForArch) {
            $fileName = if ($arch) { "python-$ver-$arch.exe" } else { "python-$ver.exe" }
            $url = "$base$fileName"
            $dest = Join-Path $env:TEMP $fileName
            Info "尝试下载: $url"
            try {
                Invoke-WebRequest -Uri $url -UseBasicParsing -OutFile $dest -ErrorAction Stop
                $size = (Get-Item $dest).Length
                if ($size -gt 0) {
                    Ok "选定安装包 $fileName (版本 $ver, 大小 $([math]::Round($size/1MB,2)) MB)"
                    return @{ Version = $ver; Path = $dest }
                }
            } catch {
                Remove-Item $dest -ErrorAction SilentlyContinue
                Warn "版本 $ver 的安装包不可用，尝试下一个版本"
            }
        }
    }
    throw "未找到可用的 Python 安装包"
}

function Install-Python {
    $installer = Get-LatestPythonInstaller
    Info "静默安装 Python $($installer.Version) ..."
    $args = "/quiet InstallAllUsers=1 PrependPath=1 Include_test=0 Include_pip=1 Include_launcher=1"
    $proc = Start-Process -FilePath $installer.Path -ArgumentList $args -Wait -PassThru
    if ($proc.ExitCode -ne 0) {
        throw "安装失败，退出码 $($proc.ExitCode)"
    }
    Ok "Python 安装完成"
    $py = Get-PythonPath
    if (-not $py) {
        $default = Join-Path "C:\\Program Files" "Python$($installer.Version.Replace('.',''))" "python.exe"
        if (Test-Path $default) { $py = $default }
    }
    if (-not $py) { throw "安装后未找到 python.exe" }
    return $py
}

function Ensure-PipMirror {
    param([string]$Python)
    $pipDir = Join-Path $env:APPDATA "pip"
    if (-not (Test-Path $pipDir)) { New-Item -ItemType Directory -Path $pipDir | Out-Null }
    $pipIni = Join-Path $pipDir "pip.ini"
    $content = @"
[global]
index-url = https://pypi.tuna.tsinghua.edu.cn/simple
trusted-host = pypi.tuna.tsinghua.edu.cn
"@
    $content | Out-File -FilePath $pipIni -Encoding ASCII -Force
    Ok "已设置 pip 镜像: $pipIni"
}

function Ensure-Packages {
    param([string]$Python, [string]$RepoRoot)
    $req = Join-Path $RepoRoot "requirements.txt"
    if (-not (Test-Path $req)) {
        Warn "缺少 requirements.txt，跳过依赖安装"
        return
    }
    Info "升级 pip..."
    & $Python -m pip install --upgrade pip --index-url https://pypi.tuna.tsinghua.edu.cn/simple
    Info "安装依赖..."
    & $Python -m pip install -r $req --index-url https://pypi.tuna.tsinghua.edu.cn/simple
}

function Start-App {
    param([string]$Python, [string]$RepoRoot)
    Set-Location $RepoRoot
    $scriptPath = Join-Path $RepoRoot "course_selector.py"
    if (-not (Test-Path $scriptPath)) {
        throw "未找到 course_selector.py"
    }
    Info "启动程序..."
    & $Python $scriptPath
}

# main
$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $repoRoot
Info "工作目录: $repoRoot"

$pythonPath = Get-PythonPath
if ($pythonPath) {
    Ok "检测到现有 Python: $pythonPath"
} else {
    Warn "未检测到 Python，开始静默安装"
    # 请求管理员权限（为所有用户安装需要管理员权限）
    Request-AdminPrivilege
    $pythonPath = Install-Python
    Ok "使用新安装的 Python: $pythonPath"
}

Ensure-PipMirror -Python $pythonPath
Ensure-Packages -Python $pythonPath -RepoRoot $repoRoot
Start-App -Python $pythonPath -RepoRoot $repoRoot
