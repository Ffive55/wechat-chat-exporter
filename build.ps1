param([string]$Python='python',[string]$DistPath=(Join-Path $PSScriptRoot 'dist'))
$ErrorActionPreference='Stop'
$root=$PSScriptRoot
& $Python -m pip install -r (Join-Path $root 'requirements.txt')
if($LASTEXITCODE){throw '安装构建依赖失败'}
# Some Python runtimes load this certificate policy file beside ssl.py when an
# SSLContext is created. PyInstaller collects the module but not this data file.
$sslSupport = & $Python -c "import pathlib,ssl; p=pathlib.Path(ssl.__file__).with_name('codex-rejected-cas.pem'); print(p if p.is_file() else '')"
if($LASTEXITCODE){throw '检查 SSL 运行时资源失败'}
$extraData = @()
if($sslSupport){$extraData += @('--add-data',"$sslSupport;.")}
& $Python -m PyInstaller --noconfirm --onefile --windowed --name '微信聊天导出器' --distpath $DistPath --workpath (Join-Path $root 'build') --specpath $root --paths $root --add-data "$(Join-Path $root 'assets');assets" @extraData --icon (Join-Path $root 'assets\app.ico') --collect-all webview --collect-all pythonnet --collect-all clr_loader --collect-all zstandard --hidden-import clr --hidden-import pefile --collect-submodules Crypto --exclude-module numpy --exclude-module tkinter (Join-Path $root 'main.py')
if($LASTEXITCODE){throw 'EXE 构建失败'}
