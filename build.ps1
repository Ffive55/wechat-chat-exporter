param([string]$Python='python')
$ErrorActionPreference='Stop'
$root=$PSScriptRoot
& $Python -m pip install -r (Join-Path $root 'requirements.txt')
if($LASTEXITCODE){throw '安装构建依赖失败'}
& $Python -m PyInstaller --noconfirm --onefile --windowed --name '微信聊天导出器' --distpath (Join-Path $root 'dist') --workpath (Join-Path $root 'build') --specpath $root --paths $root --add-data "$(Join-Path $root 'assets');assets" --icon (Join-Path $root 'assets\app.ico') --collect-all webview --collect-all pythonnet --collect-all clr_loader --collect-all zstandard --hidden-import clr --hidden-import pefile --collect-submodules Crypto --exclude-module numpy --exclude-module tkinter (Join-Path $root 'main.py')
if($LASTEXITCODE){throw 'EXE 构建失败'}
