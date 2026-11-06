$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$installedData = Join-Path $PSScriptRoot 'dist\Parcel\data'
$installedExe = Join-Path $PSScriptRoot 'dist\Parcel\Parcel.exe'
$staging = Join-Path $PSScriptRoot 'build\package'
$stagedParcel = Join-Path $staging 'Parcel'
$runningParcel = Get-Process Parcel -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $installedExe }
if ($runningParcel) { throw '请先从托盘菜单退出 Parcel，再重新生成程序。' }
$dataBackup = $null
if (Test-Path -LiteralPath $installedData) {
    $dataBackup = Join-Path $PSScriptRoot ('data\build-backups\' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    New-Item -ItemType Directory -Force -Path $dataBackup | Out-Null
    Get-ChildItem -LiteralPath $installedData | Copy-Item -Destination $dataBackup -Recurse
}
python -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw '检查未通过，停止打包。' }
if (Test-Path -LiteralPath $stagedParcel) {
    $resolvedStage = (Get-Item -LiteralPath $stagedParcel).FullName
    if ($resolvedStage -ne $stagedParcel) { throw '打包目录不正确。' }
    foreach ($folder in @('build', 'build\package', 'build\package\Parcel')) {
        if ((Get-Item -LiteralPath $folder).Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw '打包目录不可使用目录联接。'
        }
    }
}
python -m PyInstaller --noconfirm --clean --onedir --windowed --name Parcel --distpath $staging --icon assets/tomato.ico --add-data 'assets;assets' --collect-data tzdata parcel.py
if ($LASTEXITCODE -ne 0) { throw '打包失败。' }
if (Test-Path -LiteralPath $installedExe) {
    try {
        $access = [IO.File]::Open($installedExe, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
        $access.Dispose()
    } catch [IO.IOException] {
        throw '新版已打包。请从托盘菜单彻底退出旧版 Parcel，再运行打包脚本更新桌面版。'
    }
}
$destination = Split-Path -Parent $installedExe
New-Item -ItemType Directory -Force -Path $installedData | Out-Null
Copy-Item -LiteralPath (Join-Path $stagedParcel '_internal') -Destination $destination -Recurse -Force
Copy-Item -LiteralPath (Join-Path $stagedParcel 'Parcel.exe') -Destination $installedExe -Force
$sourceData = Join-Path $PSScriptRoot 'data\parcel.json'
if ((Test-Path -LiteralPath $sourceData) -and -not (Test-Path -LiteralPath (Join-Path $installedData 'parcel.json'))) {
    Copy-Item -LiteralPath $sourceData -Destination (Join-Path $installedData 'parcel.json')
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot '使用说明.md') -Destination (Join-Path $PSScriptRoot 'dist\Parcel\使用说明.md')
Write-Output '已生成 dist\Parcel\Parcel.exe'
