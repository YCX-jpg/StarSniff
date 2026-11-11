#Requires -Version 5.1
<#
.SYNOPSIS
    构建 StarSniff 单文件 exe，并打成可直接上传 GitHub Releases 的 zip。

.DESCRIPTION
    版本号自动读取 resource_sniffer/__init__.py 的 __version__。
    输出：
      dist/StarSniff.exe
      dist/StarSniff-<version>-win64.zip
      dist/StarSniff-<version>-win64.zip.sha256

.EXAMPLE
    .\build_exe.ps1
    .\build_exe.ps1 -SkipTests
    .\build_exe.ps1 -Publish   # 需要已安装并登录 gh
#>
param(
    [switch]$SkipTests,
    [switch]$Publish,
    [string]$Python = "",
    [string]$ReleaseDir = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if (-not $Python) {
    if (Test-Path ".venv\Scripts\python.exe") {
        $Python = (Resolve-Path ".venv\Scripts\python.exe").Path
    } else {
        $Python = "python"
    }
}
Write-Host "使用 Python: $Python" -ForegroundColor Cyan

$Version = (& $Python -c "import sys; sys.path.insert(0, r'$Root'); from resource_sniffer import __version__; print(__version__)").Trim()
if (-not $Version) { throw "无法读取版本号" }
Write-Host "StarSniff 版本: $Version" -ForegroundColor Cyan

# 让 exe 的文件属性版本与代码版本保持一致（版本号只需改 __init__.py）
$parts = @($Version.Split(".") | ForEach-Object { [int]$_ })
while ($parts.Count -lt 4) { $parts += 0 }
$viPath = Join-Path $Root "version_info.txt"
$vi = Get-Content $viPath -Raw
$vi = $vi -replace "filevers=\([^)]*\)", "filevers=($($parts[0]), $($parts[1]), $($parts[2]), $($parts[3]))"
$vi = $vi -replace "prodvers=\([^)]*\)", "prodvers=($($parts[0]), $($parts[1]), $($parts[2]), $($parts[3]))"
$vi = $vi -replace "StringStruct\('FileVersion', '[^']*'\)", "StringStruct('FileVersion', '$($parts[0]).$($parts[1]).$($parts[2]).$($parts[3])')"
$vi = $vi -replace "StringStruct\('ProductVersion', '[^']*'\)", "StringStruct('ProductVersion', '$Version')"
Set-Content -Path $viPath -Value $vi -Encoding ascii

if (-not $SkipTests) {
    Write-Host "运行自检..." -ForegroundColor Cyan
    $env:PYTHONIOENCODING = "utf-8"
    & $Python tests\selftest.py
    if ($LASTEXITCODE -ne 0) { throw "自检失败，已停止打包" }
}

Write-Host "检查 PyInstaller..." -ForegroundColor Cyan
& $Python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "安装 PyInstaller..." -ForegroundColor Yellow
    & $Python -m pip install --disable-pip-version-check pyinstaller
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 安装失败" }
}

Write-Host "清理 build/dist..." -ForegroundColor Cyan
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue

Write-Host "开始打包目录版（推荐，启动快、不依赖临时目录）..." -ForegroundColor Cyan
& $Python -m PyInstaller --noconfirm --clean StarSniff.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 目录版打包失败" }

$ExeDir = Join-Path $Root "dist\StarSniff"
$Exe = Join-Path $ExeDir "StarSniff.exe"
if (-not (Test-Path $Exe)) { throw "没有生成 dist\StarSniff\StarSniff.exe" }

$Stage = Join-Path $Root "dist\package"
New-Item -ItemType Directory -Force $Stage | Out-Null
Copy-Item (Join-Path $ExeDir "*") $Stage -Recurse
Copy-Item README.md, LICENSE, assets\starsniff.png $Stage -ErrorAction SilentlyContinue

$ZipName = "StarSniff-$Version-win64.zip"
$ZipPath = Join-Path $Root "dist\$ZipName"
Compress-Archive -Path (Join-Path $Stage "*") -DestinationPath $ZipPath -Force
Remove-Item -Recurse -Force $Stage

Write-Host "开始打包单文件便携版..." -ForegroundColor Cyan
& $Python -m PyInstaller --noconfirm --clean StarSniff-onefile.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 单文件版打包失败" }
$Portable = Join-Path $Root "dist\StarSniff-portable.exe"
if (-not (Test-Path $Portable)) { throw "没有生成 dist\StarSniff-portable.exe" }

$Hash = (Get-FileHash $ZipPath -Algorithm SHA256).Hash.ToLower()
"$Hash  $ZipName" | Set-Content "$ZipPath.sha256" -Encoding ascii
$PortableHash = (Get-FileHash $Portable -Algorithm SHA256).Hash.ToLower()
"$PortableHash  StarSniff-portable.exe" |
    Set-Content "$Portable.sha256" -Encoding ascii

$ExeSize = [math]::Round((Get-Item $Portable).Length / 1MB, 1)
$ZipSize = [math]::Round((Get-Item $ZipPath).Length / 1MB, 1)

# 本地版本目录（code\v<版本>\）旁边有 Releases\ 时自动归档；
# 标准 GitHub 仓库根目录没有 Releases\，只产出 dist\，由 Actions 上传。
if (-not $ReleaseDir) {
    $RepoRoot = Split-Path -Parent (Split-Path -Parent $Root)
    $MaybeReleases = Join-Path $RepoRoot "Releases"
    if (Test-Path $MaybeReleases) {
        $ReleaseDir = Join-Path $MaybeReleases "v$Version"
    }
}
if ($ReleaseDir) {
    New-Item -ItemType Directory -Force $ReleaseDir | Out-Null
    Copy-Item -Path @($ZipPath, "$ZipPath.sha256", $Portable, "$Portable.sha256") `
        -Destination $ReleaseDir -Force
    Copy-Item -Path @("README.md", "LICENSE", "RELEASE_NOTES.md",
        "assets\starsniff.png") -Destination $ReleaseDir -Force
}

Write-Host ""
Write-Host "完成 ✔" -ForegroundColor Green
Write-Host "  dist\StarSniff\StarSniff.exe        目录版（zip 内）"
Write-Host "  dist\$ZipName   $ZipSize MB"
Write-Host "  dist\StarSniff-portable.exe         $ExeSize MB（单文件）"
if ($ReleaseDir) {
    Write-Host "  归档: $ReleaseDir"
} else {
    Write-Host "  本地归档: 跳过（标准仓库只保留 dist）"
}
Write-Host "  SHA256: $Hash"

if ($Publish) {
    $Gh = Get-Command gh -ErrorAction SilentlyContinue
    if (-not $Gh) { throw "未安装 GitHub CLI（gh），无法自动发布" }
    $Tag = "v$Version"
    Write-Host "创建/更新 GitHub Release $Tag ..." -ForegroundColor Cyan
    & gh release view $Tag 2>$null
    if ($LASTEXITCODE -eq 0) {
        & gh release upload $Tag $ZipPath "$ZipPath.sha256" `
            $Portable "$Portable.sha256" --clobber
    } else {
        & gh release create $Tag $ZipPath "$ZipPath.sha256" `
            $Portable "$Portable.sha256" `
            --title "StarSniff $Version" --generate-notes
    }
    if ($LASTEXITCODE -ne 0) { throw "GitHub Release 发布失败" }
    Write-Host "已发布：https://github.com/YCX-jpg/StarSniff/releases/tag/$Tag" -ForegroundColor Green
}
