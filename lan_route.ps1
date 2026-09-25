<#
.SYNOPSIS
    Возвращает маршрут в домашнюю сеть на сетевую карту, не трогая VPN.

.DESCRIPTION
    Проблема: некоторые VPN-клиенты (Amnezia в том числе) прописывают маршрут
    на локальную подсеть через себя с метрикой 0. После этого пакеты к соседним
    устройствам — телефону, принтеру, NAS — уходят в туннель и не доходят.

    Скрипт добавляет маршрут на локальную подсеть через роутер с метрикой 1,
    и Windows начинает предпочитать его. Весь остальной трафик по-прежнему
    уходит в VPN: маршрут по умолчанию не трогается.

    Побочный эффект, который нам и нужен: ZeroTier получает прямой путь к
    устройствам в той же сети вместо ретрансляции через свои серверы.

    ЗАПУСКАТЬ ОТ АДМИНИСТРАТОРА. Откат: -Off

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File lan_route.ps1
    powershell -ExecutionPolicy Bypass -File lan_route.ps1 -Off
#>
param([switch]$Off)

$ErrorActionPreference = "Stop"

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal $id).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Admin)) {
    Write-Host "Нужны права администратора." -ForegroundColor Red
    Write-Host "Открой PowerShell от имени администратора и выполни:"
    Write-Host "  powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit 1
}

# --- физический интерфейс с локальным адресом --------------------------------
$lan = Get-NetIPAddress -AddressFamily IPv4 |
       Where-Object { $_.IPAddress -match '^(192\.168|10\.|172\.(1[6-9]|2\d|3[01])\.)' -and
                      $_.InterfaceAlias -notmatch 'Amnezia|ZeroTier|Tailscale|WireGuard|Loopback|vEthernet' } |
       Sort-Object InterfaceMetric | Select-Object -First 1

if (-not $lan) {
    Write-Host "Не нашёл физический интерфейс с локальным адресом." -ForegroundColor Red
    exit 1
}

$prefix = "$($lan.IPAddress -replace '\.\d+$', '.0')/$($lan.PrefixLength)"
$gateway = ($lan.IPAddress -replace '\.\d+$', '.1')
Write-Host "Интерфейс: $($lan.InterfaceAlias), адрес $($lan.IPAddress), подсеть $prefix"

# Две половинки подсети. Windows выбирает маршрут по длине префикса, и только
# при равной длине смотрит на метрику. Поэтому /25 побеждает /24 от VPN,
# как бы низко тот ни выставил метрику. Маршрут по умолчанию не затрагивается,
# весь остальной трафик по-прежнему уходит в VPN.
$base = $lan.IPAddress -replace '\.\d+$', ''
$halves = @("$base.0/25", "$base.128/25")

if ($Off) {
    foreach ($p in @($prefix) + $halves) {
        Get-NetRoute -DestinationPrefix $p -InterfaceIndex $lan.InterfaceIndex `
                     -ErrorAction SilentlyContinue |
            Where-Object { $_.NextHop -eq $gateway } |
            Remove-NetRoute -Confirm:$false
    }
    Write-Host "Маршруты сняты. Локальная сеть снова уходит в VPN." -ForegroundColor Green
    exit 0
}

# --- кто сейчас забирает локальную подсеть -----------------------------------
$current = Get-NetRoute -DestinationPrefix $prefix -ErrorAction SilentlyContinue |
           Sort-Object RouteMetric
foreach ($r in $current) {
    $alias = (Get-NetIPInterface -InterfaceIndex $r.InterfaceIndex -AddressFamily IPv4).InterfaceAlias
    Write-Host ("  сейчас: {0,-16} метрика {1}" -f $alias, $r.RouteMetric)
}

foreach ($p in $halves) {
    $exists = Get-NetRoute -DestinationPrefix $p -InterfaceIndex $lan.InterfaceIndex `
                           -ErrorAction SilentlyContinue |
              Where-Object { $_.NextHop -eq $gateway }
    if ($exists) {
        $exists | Set-NetRoute -RouteMetric 1 -Confirm:$false
        Write-Host "Маршрут уже был: $p"
    } else {
        New-NetRoute -DestinationPrefix $p -InterfaceIndex $lan.InterfaceIndex `
                     -NextHop $gateway -RouteMetric 1 -Confirm:$false | Out-Null
        Write-Host "Маршрут добавлен: $p через $gateway"
    }
}

# --- подсеть ZeroTier --------------------------------------------------------
# Kill-switch VPN забирает себе и её тоже. Маршрутами это не лечится: клиент
# дублирует любой новый маршрут с метрикой 0. Зато можно поднять приоритет
# самого интерфейса ZeroTier — это безопасно, потому что маршрута по умолчанию
# он не объявляет (пока в приложении не включён Route all traffic).
$zt = Get-NetAdapter | Where-Object { $_.InterfaceDescription -like "*ZeroTier*" -and $_.Status -eq "Up" } |
      Select-Object -First 1
if ($zt) {
    $ztRoutes = Get-NetRoute -InterfaceIndex $zt.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
                Where-Object { $_.DestinationPrefix -notmatch '^(0\.0\.0\.0/0|255\.|224\.)' -and
                               $_.DestinationPrefix -notlike "*/32" }
    if ($Off) {
        Set-NetIPInterface -InterfaceIndex $zt.ifIndex -AddressFamily IPv4 -AutomaticMetric Enabled
        Write-Host "ZeroTier: приоритет интерфейса возвращён на автоматический."
    } else {
        Set-NetIPInterface -InterfaceIndex $zt.ifIndex -AddressFamily IPv4 -InterfaceMetric 1
        foreach ($r in $ztRoutes) {
            Set-NetRoute -DestinationPrefix $r.DestinationPrefix -InterfaceIndex $zt.ifIndex `
                         -RouteMetric 1 -Confirm:$false -ErrorAction SilentlyContinue
            Write-Host "ZeroTier: приоритет поднят для $($r.DestinationPrefix)"
        }
    }
}

# --- проверка ----------------------------------------------------------------
function Show-Path($target, $label) {
    $r = Find-NetRoute -RemoteIPAddress $target -ErrorAction SilentlyContinue | Select-Object -Last 1
    if ($r) {
        $alias = (Get-NetIPInterface -InterfaceIndex $r.InterfaceIndex -AddressFamily IPv4).InterfaceAlias
        $good = $alias -notmatch 'Amnezia|WireGuard'
        Write-Host ("Проверка: {0} -> через {1}" -f $label, $alias) `
                   -ForegroundColor ($(if ($good) { "Green" } else { "Yellow" }))
    }
}
Write-Host ""
Show-Path ($lan.IPAddress -replace '\.\d+$', '.105') "локальная сеть"
if ($zt) {
    $ztIp = Get-NetIPAddress -InterfaceIndex $zt.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object { $_.IPAddress -notlike "169.254.*" } | Select-Object -First 1
    if ($ztIp) { Show-Path ($ztIp.IPAddress -replace '\.\d+$', '.172') "сеть ZeroTier" }
}
Write-Host "Откат: powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Off"
