#!/bin/bash
export WINEPREFIX=~/.wine-steam
WINE="arch -x86_64 /Applications/Wine Stable.app/Contents/Resources/wine/bin/wine"
STEAM_EXE="$WINEPREFIX/drive_c/Program Files (x86)/Steam/steam.exe"

$WINE "$STEAM_EXE" -no-cef-sandbox -noreactlogin 2>/dev/null &
echo "Steam 已啟動"
