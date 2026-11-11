#!/bin/zsh
set -e
cd "${0:A:h}"
source_app="$PWD/dist/Tomato.app"
target_app="$HOME/Applications/Tomato.app"
if [[ ! -d "$source_app" ]]; then
  print '找不到 Tomato.app，请保留安装脚本与 dist 文件夹的相对位置。'
  read '?按回车退出'
  exit 1
fi
mkdir -p "$HOME/Applications"
if [[ -e "$target_app" ]]; then
  mv "$target_app" "$HOME/Applications/Tomato-backup-$(date +%Y%m%d-%H%M%S).app"
fi
/usr/bin/ditto "$source_app" "$target_app"
if [[ ! -e "$HOME/Desktop/Tomato.app" && ! -L "$HOME/Desktop/Tomato.app" ]]; then
  ln -s "$target_app" "$HOME/Desktop/Tomato.app"
fi
if [[ -f "$HOME/Desktop/Parcel预览.command" ]]; then
  mv "$HOME/Desktop/Parcel预览.command" "$HOME/Desktop/Parcel预览（旧原型）.command"
fi
open "$target_app"
