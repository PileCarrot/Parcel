param([switch]$PackageOnly, [switch]$InstallOnly)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if ($PackageOnly -and $InstallOnly) { throw '请选择仅打包或仅安装。' }
$installedData = Join-Path $PSScriptRoot 'dist\Parcel\data'
$installedExe = Join-Path $PSScriptRoot 'dist\Parcel\Parcel.exe'
$staging = Join-Path $PSScriptRoot 'build\package'
$stagedParcel = Join-Path $staging 'Parcel'
$stagedExe = Join-Path $stagedParcel 'Parcel.exe'
$stagedRuntime = Join-Path $stagedParcel '_internal'
foreach ($folder in @('build', 'build\package', 'build\package\Parcel', 'build\Parcel', 'build\pyinstaller-cache')) {
    $target = Join-Path $PSScriptRoot $folder
    if (Test-Path -LiteralPath $target) {
        $item = Get-Item -LiteralPath $target
        if ($item.FullName -ne $target -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw '打包目录不正确或使用了目录联接。'
        }
    }
}
if (-not $InstallOnly) {
    python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw '检查未通过，停止打包。' }
    $env:PYINSTALLER_CONFIG_DIR = Join-Path $PSScriptRoot 'build\pyinstaller-cache'
    python -m PyInstaller --noconfirm --clean --onedir --windowed --name Parcel --distpath $staging --icon assets/tomato.ico --add-data 'assets;assets' --collect-data tzdata parcel.py
    if ($LASTEXITCODE -ne 0) { throw '打包失败。' }
    if ($PackageOnly) {
        Write-Output '已生成 build\package\Parcel\Parcel.exe'
        return
    }
}
if (-not (Test-Path -LiteralPath $stagedExe -PathType Leaf) -or
    -not (Test-Path -LiteralPath $stagedRuntime -PathType Container)) {
    throw '找不到已打包的 Parcel，请先打包。'
}
if (Test-Path -LiteralPath $installedExe) {
    try {
        $access = [IO.File]::Open($installedExe, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
        $access.Dispose()
    } catch [IO.IOException] {
        throw '新版已打包。请从托盘菜单彻底退出旧版 Parcel，再运行 build.ps1 -InstallOnly。'
    }
}
if (Test-Path -LiteralPath $installedData) {
    $dataBackup = Join-Path $PSScriptRoot ('data\build-backups\' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
    New-Item -ItemType Directory -Force -Path $dataBackup | Out-Null
    Get-ChildItem -LiteralPath $installedData | Copy-Item -Destination $dataBackup -Recurse
}
$destination = Split-Path -Parent $installedExe
New-Item -ItemType Directory -Force -Path $installedData | Out-Null
Copy-Item -LiteralPath $stagedRuntime -Destination $destination -Recurse -Force
Copy-Item -LiteralPath $stagedExe -Destination $installedExe -Force
$sourceData = Join-Path $PSScriptRoot 'data\parcel.json'
if ((Test-Path -LiteralPath $sourceData) -and -not (Test-Path -LiteralPath (Join-Path $installedData 'parcel.json'))) {
    Copy-Item -LiteralPath $sourceData -Destination (Join-Path $installedData 'parcel.json')
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot '使用说明.md') -Destination (Join-Path $PSScriptRoot 'dist\Parcel\使用说明.md')
Write-Output '已生成 dist\Parcel\Parcel.exe'
