<#
    FOREST's Proxy Auto-updater — сборка одного ForestProxyUpdater.exe.

    Что делает (идемпотентно, можно запускать сколько угодно раз):
      1. находит 64-битный Python 3;
      2. поднимает изолированное окружение packaging\.build-venv и ставит в него
         PyInstaller (пин из requirements-build.txt), если его ещё нет;
      3. добивает входные данные: ядро Xray-core (fetch_core.py, только
         официальные релизы XTLS) и ПРОВЕРЯЕТ иконку пользователя app.ico
         (её хеш записан в app.ico.sha256); если иконки нет — сборка
         останавливается, а не подменяет рисунок сгенерированным;
      4. запускает PyInstaller по спеке ForestProxyUpdater.spec;
      5. кладёт результат в packaging\dist\ и печатает размер и SHA256.

    Примеры:
      powershell -ExecutionPolicy Bypass -File packaging\build.ps1
      powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -Clean      # с нуля
      powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -NoVenv    # без venv
      $env:FOREST_PYTHON='C:\Path\to\python.exe'; ...\build.ps1
#>
#Requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$Clean,     # снести .build-venv, build\, dist\ перед сборкой
    [switch]$NoVenv,    # ставить PyInstaller в системный Python, без venv
    [string]$Python = '', # явный путь к python.exe
    [switch]$GenerateIcon, # ЯВНО разрешить нарисовать app.ico (make_icon.py)
    [string]$Helper = ''   # путь к собранному forest-ui.exe (по умолчанию dist\)
)

# ВНИМАНИЕ: именно 'Continue'. При 'Stop' любой вывод нативного процесса в stderr
# (pip, py, PyInstaller) превращается в исключение и роняет сборку на ровном месте.
# ВНИМАНИЕ: именно 'Continue'. При 'Stop' любой вывод нативного процесса в stderr
# (pip, py, PyInstaller) превращается в исключение и роняет сборку на ровном месте.
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Here

$VenvDir = Join-Path $Here '.build-venv'
$BuildDir = Join-Path $Here 'build'
$DistDir = Join-Path $Here 'dist'
$ExePath = Join-Path $DistDir 'ForestProxyUpdater.exe'
$SpecPath = Join-Path $Here 'ForestProxyUpdater.spec'
$IconPath = Join-Path $Here 'app.ico'
$CoreExe = Join-Path $Here 'vendor\bin\xray\xray.exe'
$ReqPath = Join-Path $Here 'requirements-build.txt'
$HelperPath = if ($Helper) { $Helper } else { Join-Path $DistDir 'forest-ui.exe' }

function Info($m) { Write-Host "[build] $m" }
function Die($m) { Write-Host "[build] ОШИБКА: $m" -ForegroundColor Red; exit 1 }

# Только строки stdout, отбрасывая возможные ErrorRecord от stderr.
function Select-Stdout {
    param($Out, [string]$Prefix)
    return ($Out | Where-Object { $_ -is [string] -and $_ -like "$Prefix*" } |
            Select-Object -Last 1)
}

# ────────────────────────────────────── 1. найти python 3 (64-бит) ───
function Get-PythonExe {
    param([string]$Explicit)
    # Одинарные кавычки ВНУТРИ кода и двойные снаружи: PowerShell 5.1 портит
    # двойные кавычки внутри аргумента нативного процесса.
    $probe = "import sys;print('PYEXE:'+sys.executable) if (sys.version_info[0]==3 and sys.maxsize>2**32) else sys.exit(3)"
    $cands = @()
    if ($Explicit) { $cands += , @($Explicit) }
    elseif ($env:FOREST_PYTHON) { $cands += , @($env:FOREST_PYTHON) }
    $cands += , @('py', '-3.12')
    $cands += , @('py', '-3.13')
    $cands += , @('py', '-3.11')
    $cands += , @('py', '-3')
    $cands += , @('python')
    $cands += , @('python3')
    foreach ($c in $cands) {
        $exe = $c[0]
        $pre = @()
        if ($c.Count -gt 1) { $pre = $c[1..($c.Count - 1)] }
        $out = & $exe @pre -c $probe 2>&1
        $code = $LASTEXITCODE
        $line = Select-Stdout $out 'PYEXE:'
        if ($code -eq 0 -and $line) {
            return @{ Exe = $exe; Pre = $pre; Path = $line.Substring(6) }
        }
    }
    return $null
}

# Проверка без трассировки: find_spec не бросает ImportError.
function Get-PyInstallerVersion {
    param($Py)
    $null = & $Py.Exe @($Py.Pre) -c "import importlib.util as u,sys;sys.exit(0 if u.find_spec('PyInstaller') else 1)" 2>&1
    if ($LASTEXITCODE -ne 0) { return $null }
    $out = & $Py.Exe @($Py.Pre) -c "import PyInstaller;print('PYI:'+PyInstaller.__version__)" 2>&1
    if ($LASTEXITCODE -ne 0) { return $null }
    $line = Select-Stdout $out 'PYI:'
    if (-not $line) { return $null }
    return $line.Substring(4)
}

function Invoke-Py {
    param($Py, [string[]]$ArgsList)
    Write-Host ("[build]   > python " + ($ArgsList -join ' ')) -ForegroundColor DarkGray
    & $Py.Exe @($Py.Pre) @ArgsList 2>&1 | ForEach-Object { Write-Host "    $_" }
    if ($LASTEXITCODE -ne 0) {
        Die ("python " + ($ArgsList -join ' ') + " вернул код $LASTEXITCODE")
    }
}

# ────────────────────────────────────── 2. venv + PyInstaller ───
if ($Clean) {
    Info "чищу прошлые артефакты (-Clean)"
    Remove-Item -Recurse -Force $VenvDir, $BuildDir, $DistDir -ErrorAction SilentlyContinue
}

$base = Get-PythonExe -Explicit $Python
if (-not $base) { Die 'не найден 64-битный Python 3 (python / py -3 / python3)' }
Info "базовый Python: $($base.Path)"

if ($NoVenv) {
    $py = $base
    $where = 'системный Python'
} else {
    $venvPy = Join-Path $VenvDir 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPy)) {
        Info "создаю окружение сборки: $VenvDir"
        Invoke-Py $base @('-m', 'venv', $VenvDir)
    } else {
        Info "переиспользую окружение сборки: $VenvDir"
    }
    if (-not (Test-Path -LiteralPath $venvPy)) { Die "venv не создан: $venvPy" }
    $py = @{ Exe = $venvPy; Pre = @(); Path = $venvPy }
    $where = 'packaging\.build-venv'
}

$pyi = Get-PyInstallerVersion $py
if (-not $pyi) {
    Info "PyInstaller не найден ($where) — устанавливаю из requirements-build.txt"
    Invoke-Py $py @('-m', 'pip', 'install', '--upgrade', '--disable-pip-version-check', '-r', $ReqPath)
    $pyi = Get-PyInstallerVersion $py
    if (-not $pyi) { Die 'PyInstaller не установился' }
}
Info "PyInstaller $pyi ($where)"

# ────────────────────────────────────── 3. ядро и иконка ───
if (-not (Test-Path -LiteralPath $CoreExe)) {
    Info 'ядра xray нет — качаю официальный релиз XTLS/Xray-core'
    Invoke-Py $py @((Join-Path $Here 'fetch_core.py'))
} else {
    Info 'ядро xray уже на месте (vendor\bin\xray)'
}
if (-not (Test-Path -LiteralPath $IconPath)) {
    if ($GenerateIcon) {
        Info 'иконки нет — рисую app.ico (make_icon.py, ЯВНО разрешено -GenerateIcon)'
        Invoke-Py $py @((Join-Path $Here 'make_icon.py'))
    } else {
        Die ("нет иконки пользователя: $IconPath`n" +
             "        Это РИСУНОК ПОЛЬЗОВАТЕЛЯ, а не сгенерированная картинка.`n" +
             "        Верните её из репозитория:  git checkout -- packaging/app.ico`n" +
             "        (или соберите .ico из PNG и положите в packaging\app.ico).`n" +
             "        Осознанно нарисовать новую:  build.ps1 -GenerateIcon")
    }
} else {
    # Сверка с записанным хешем: молчаливая подмена иконки ломала значок трея
    # (пользователь видел чужой рисунок), и это замечалось только глазами.
    $IconHash = (Get-FileHash -LiteralPath $IconPath -Algorithm SHA256).Hash.ToLower()
    $IconPin = Join-Path $Here 'app.ico.sha256'
    $wantIcon = ''
    if (Test-Path -LiteralPath $IconPin) {
        foreach ($l in @(Get-Content -LiteralPath $IconPin -ErrorAction SilentlyContinue)) {
            $s = ([string]$l).Trim()
            if ($s -and -not $s.StartsWith('#')) { $wantIcon = ($s -split '\s+')[0].ToLower(); break }
        }
    }
    if ($wantIcon -and $wantIcon -ne $IconHash) {
        Info ("ВНИМАНИЕ: app.ico НЕ совпадает с записанным хешем!`n" +
              "        ожидалось $wantIcon`n" +
              "        получено  $IconHash`n" +
              "        Если иконку обновили СОЗНАТЕЛЬНО — впишите новый хеш в packaging\app.ico.sha256.")
    } else {
        Info "иконка app.ico на месте, хеш совпадает с app.ico.sha256"
    }
    Info "  app.ico sha256=$IconHash"
}

# ────────────────────────────────────── 3б. собранный помощник ───
# Windows-версия отдаётся пользователю ОДНИМ файлом: forest-ui.exe встраивается
# внутрь основного .exe как данные (ui_bundle/) и распаковывается при первом
# запуске. Значит, к моменту сборки помощник должен быть уже собран — иначе
# получился бы .exe без значка/меню/уведомлений, то есть молчаливо урезанный.
if (-not (Test-Path -LiteralPath $HelperPath)) {
    Die ("нет собранного помощника интерфейса: $HelperPath`n" +
         "        Windows-версия ставится ОДНИМ файлом — помощник встраивается внутрь.`n" +
         "        Соберите его:  powershell -ExecutionPolicy Bypass -File packaging\build-ui.ps1`n" +
         "        (или укажите готовый: build.ps1 -Helper D:\путь\forest-ui.exe)")
}
$HelperItem = Get-Item -LiteralPath $HelperPath
$HelperHash = (Get-FileHash -LiteralPath $HelperPath -Algorithm SHA256).Hash.ToLower()
$HelperMib = [math]::Round($HelperItem.Length / 1MB, 2)
Info ("помощник интерфейса для встраивания: $($HelperItem.FullName)`n" +
      "        размер $($HelperItem.Length) байт ($HelperMib МиБ), sha256=$HelperHash`n" +
      "        собран $($HelperItem.LastWriteTime.ToString('yyyy-MM-dd HH:mm'))")
# Честное предупреждение, а не тихая подмена: помощник мог быть собран ДО
# правок интерфейса (меню/трей правятся отдельно).
foreach ($s in @('forest_ui.py', 'ui_menu.py', 'ui_style.py', 'ui_cmd.py')) {
    $srcFile = Join-Path (Join-Path (Split-Path -Parent $Here) 'src') $s
    if ((Test-Path -LiteralPath $srcFile) -and
        ((Get-Item -LiteralPath $srcFile).LastWriteTime -gt $HelperItem.LastWriteTime)) {
        Info ("ВНИМАНИЕ: src\$s новее собранного помощника — в .exe попадёт " +
              "СТАРЫЙ интерфейс. Пересоберите: build-ui.ps1")
    }
}
$env:FOREST_HELPER_EXE = $HelperItem.FullName

# ────────────────────────────────────── 4. сборка ───
# Воспроизводимость процесса: PyInstaller вшивает в PE штамп времени — фиксируем
# его, чтобы метка не «плясала» между сборками. Побитовой идентичности .exe это
# НЕ даёт: порядок обхода каталогов при упаковке архива может отличаться, поэтому
# SHA256 между сборками может меняться (проверено). Стабильно другое: сам процесс
# повторяем и не требует ручных шагов, а содержимое архива — то же.
if (-not $env:SOURCE_DATE_EPOCH) { $env:SOURCE_DATE_EPOCH = '1735689600' }  # 2025-01-01T00:00:00Z
Info "SOURCE_DATE_EPOCH=$($env:SOURCE_DATE_EPOCH) (фиксированный штамп PE)"

New-Item -ItemType Directory -Force -Path $DistDir, $BuildDir | Out-Null
Info "собираю onefile без консоли по спеке $([IO.Path]::GetFileName($SpecPath))"
Invoke-Py $py @('-m', 'PyInstaller', '--noconfirm', '--clean',
    '--distpath', $DistDir, '--workpath', $BuildDir, $SpecPath)

if (-not (Test-Path -LiteralPath $ExePath)) { Die "exe не появился: $ExePath" }

# ────────────────────────────────────── 5. отчёт ───
$fi = Get-Item -LiteralPath $ExePath
$hash = (Get-FileHash -LiteralPath $ExePath -Algorithm SHA256).Hash.ToLower()
$mib = [math]::Round($fi.Length / 1MB, 2)

# машинночитаемый отчёт рядом с exe (не зависит от кодировки консоли)
$coreRelease = ''
foreach ($l in @(Get-Content -LiteralPath (Join-Path $Here 'core_version.txt') -ErrorAction SilentlyContinue)) {
    $s = [string]$l
    if ($s -and -not $s.StartsWith('#')) { $coreRelease = $s; break }
}
$report = [ordered]@{
    exe          = $ExePath
    size_bytes   = $fi.Length
    size_mib     = $mib
    sha256       = $hash
    built_utc    = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    base_python  = $base.Path
    build_python = $where
    pyinstaller  = $pyi
    core_release = $coreRelease
    console      = $false
    onefile      = $true
    helper_path  = $HelperItem.FullName
    helper_size_bytes = $HelperItem.Length
    helper_sha256 = $HelperHash
    helper_embedded = $true
}
$report | ConvertTo-Json -Depth 4 | Out-File -Encoding utf8 (Join-Path $DistDir 'build-report.json')
"$hash  ForestProxyUpdater.exe" | Out-File -Encoding ascii (Join-Path $DistDir 'ForestProxyUpdater.exe.sha256')

Write-Host ''
Write-Host '================ готово ================' -ForegroundColor Green
Write-Host "exe:    $ExePath"
Write-Host "размер: $($fi.Length) байт ($mib МиБ)"
Write-Host "sha256: $hash"
Write-Host '=======================================' -ForegroundColor Green
exit 0
