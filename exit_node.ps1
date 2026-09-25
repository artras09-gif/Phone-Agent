<#
.SYNOPSIS
    Делает этот ПК выходной нодой для телефона: трафик телефона идёт
    через ZeroTier в этот компьютер, а дальше — в тот VPN, который тут поднят.

.DESCRIPTION
    Зачем: на Android можно держать только один VPN одновременно. Если телефону
    нужен и VPN, и удалённое управление, то единственный слот занимает ZeroTier,
    а роль VPN исполняет этот ПК — он уже сидит в своём туннеле.

    Что делает скрипт:
      1. включает пересылку пакетов на интерфейсе ZeroTier и на исходящем;
      2. поднимает NAT для подсети ZeroTier (WinNAT, без Internet Connection Sharing);
      3. переводит адаптер ZeroTier в профиль «Общественная сеть», чтобы через
         него не торчали общие папки и RDP.

    ЗАПУСКАТЬ ОТ АДМИНИСТРАТОРА. Отменить всё: -Off

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File exit_node.ps1
    powershell -ExecutionPolicy Bypass -File exit_node.ps1 -Off
#>
param(
    [switch]$Off,
    [string]$NatName = "PhoneAgentNAT"
)

$ErrorActionPreference = "Stop"

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal $id).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Admin)) {
    Write-Host "Нужны права администратора." -ForegroundColor Red
    Write-Host "Запусти PowerShell от имени администратора и повтори:"
    Write-Host "  powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit 1
}

# --- интерфейс ZeroTier -----------------------------------------------------
$zt = Get-NetAdapter | Where-Object { $_.InterfaceDescription -like "*ZeroTier*" } |
      Select-Object -First 1
if (-not $zt) {
    Write-Host "Адаптер ZeroTier не найден. Сначала присоединись к сети:" -ForegroundColor Red
    Write-Host "  & 'C:\Program Files (x86)\ZeroTier\One\zerotier-cli.bat' join <ID сети>"
    exit 1
}

$ztIp = Get-NetIPAddress -InterfaceIndex $zt.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike "169.254.*" } | Select-Object -First 1
if (-not $ztIp) {
    Write-Host "У адаптера ZeroTier нет адреса." -ForegroundColor Red
    Write-Host "Подтверди устройство в Members на my.zerotier.com и подожди минуту."
    exit 1
}

$prefix = "$($ztIp.IPAddress -replace '\.\d+$', '.0')/$($ztIp.PrefixLength)"
Write-Host "Адаптер ZeroTier: $($zt.Name), адрес $($ztIp.IPAddress), подсеть $prefix"

# --- выключение -------------------------------------------------------------
if ($Off) {
    Get-NetNat -Name $NatName -ErrorAction SilentlyContinue |
        Remove-NetNat -Confirm:$false
    Set-NetIPInterface -InterfaceIndex $zt.ifIndex -Forwarding Disabled -ErrorAction SilentlyContinue
    Write-Host "Выходная нода выключена: NAT снят, пересылка отключена." -ForegroundColor Green
    Write-Host "В настройках сети ZeroTier убери маршрут 0.0.0.0/0, если он там есть."
    exit 0
}

# --- исходящий интерфейс ----------------------------------------------------
# Тот, через который уходит маршрут по умолчанию: обычно это активный VPN.
$defaultRoute = Get-NetRoute -DestinationPrefix "0.0.0.0/0" -ErrorAction SilentlyContinue |
                Sort-Object RouteMetric | Select-Object -First 1
if ($defaultRoute) {
    $outAlias = (Get-NetAdapter -InterfaceIndex $defaultRoute.InterfaceIndex).Name
    Write-Host "Наружу трафик пойдёт через: $outAlias"
    Set-NetIPInterface -InterfaceIndex $defaultRoute.InterfaceIndex -Forwarding Enabled
} else {
    Write-Host "Маршрут по умолчанию не найден — проверь подключение." -ForegroundColor Yellow
}

# --- пересылка и NAT --------------------------------------------------------
Set-NetIPInterface -InterfaceIndex $zt.ifIndex -Forwarding Enabled

$existing = Get-NetNat -ErrorAction SilentlyContinue |
            Where-Object { $_.InternalIPInterfaceAddressPrefix -eq $prefix }
if ($existing) {
    Write-Host "NAT для $prefix уже настроен ($($existing.Name))."
} else {
    Get-NetNat -Name $NatName -ErrorAction SilentlyContinue | Remove-NetNat -Confirm:$false
    New-NetNat -Name $NatName -InternalIPInterfaceAddressPrefix $prefix | Out-Null
    Write-Host "NAT создан: $NatName для $prefix"
}

# --- безопасность адаптера --------------------------------------------------
try {
    Set-NetConnectionProfile -InterfaceIndex $zt.ifIndex -NetworkCategory Public
    Write-Host "Адаптер ZeroTier переведён в профиль «Общественная сеть»."
} catch {
    Write-Host "Не смог сменить профиль сети (не критично): $($_.Exception.Message)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Готово. Осталось на my.zerotier.com:" -ForegroundColor Green
Write-Host "  1. Managed Routes -> добавить 0.0.0.0/0 via $($ztIp.IPAddress)"
Write-Host "  2. На телефоне в приложении ZeroTier включить Allow Default Route"
Write-Host ""
Write-Host "Проверка с телефона: интернет работает, а внешний IP совпадает с IP этого ПК."
Write-Host "Откатить: powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Off"
