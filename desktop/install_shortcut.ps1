$ErrorActionPreference = 'Stop'
$parcelExe = Join-Path $PSScriptRoot 'dist\Parcel\Parcel.exe'
if (-not (Test-Path -LiteralPath $parcelExe)) { throw '请先生成 Parcel.exe' }
$desktopFolder = [Environment]::GetFolderPath('Desktop')
$shortcutFile = Join-Path $desktopFolder 'Parcel 日程与番茄钟.lnk'
$shortcutShell = New-Object -ComObject WScript.Shell
$parcelShortcut = $shortcutShell.CreateShortcut($shortcutFile)
$parcelShortcut.TargetPath = $parcelExe
$parcelShortcut.WorkingDirectory = Split-Path -Parent $parcelExe
$parcelShortcut.IconLocation = "$parcelExe,0"
$parcelShortcut.Description = 'Parcel 本地自动排程与悬浮番茄钟'
$parcelShortcut.Save()
Write-Output $shortcutFile
