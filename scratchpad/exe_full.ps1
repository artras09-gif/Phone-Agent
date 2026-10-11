# Сквозная проверка собранного exe «как на чужом ПК»: пустые папки, adb не в PATH,
# бот с настоящим токеном (из $env:PA_TEST_TOKEN — в файлы теста не пишется),
# окна консоли, закрытие окна, удаление. Токен живёт только в изолированной папке
# и стирается удалением.
$dir = Join-Path $env:TEMP "pa_exe_full"
$key = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\PhoneAgent"
Get-Process PhoneAgent -ErrorAction SilentlyContinue | Stop-Process -Force
if ((Test-Path $key) -and ((Get-ItemProperty $key).InstallLocation -like "*\Temp\pa_*")) { Remove-Item $key }
if (Test-Path $dir) { Remove-Item -LiteralPath $dir -Recurse -Force -ErrorAction SilentlyContinue }
foreach ($d in "app","local\PhoneAgent","roaming\Microsoft\Windows\Start Menu\Programs","tmp") { New-Item -ItemType Directory -Force (Join-Path $dir $d) | Out-Null }
Copy-Item (Join-Path $env:USERPROFILE "Desktop\PhoneAgent.exe") (Join-Path $dir "app\PhoneAgent.exe")
$owner = (Get-Content "C:\Users\Admin\Desktop\PhoneAgent\telegram.json" -Raw -Encoding UTF8 | ConvertFrom-Json).owner
$tg = @{ token = $env:PA_TEST_TOKEN; owner = "$owner" } | ConvertTo-Json
[IO.File]::WriteAllText((Join-Path $dir "local\PhoneAgent\telegram.json"), $tg, (New-Object Text.UTF8Encoding $false))
$env:LOCALAPPDATA = Join-Path $dir "local"; $env:APPDATA = Join-Path $dir "roaming"
$env:TEMP = Join-Path $dir "tmp"; $env:TMP = $env:TEMP
$env:PATH = (($env:PATH -split ';') | Where-Object { $_ -and -not (Test-Path (Join-Path $_ "adb.exe")) }) -join ';'
$exe = Join-Path $dir "app\PhoneAgent.exe"; $data = Join-Path $dir "local\PhoneAgent"
function Up { try { (Invoke-WebRequest "http://127.0.0.1:8765/api/state?since=0" -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200 } catch { $false } }
function Mark($ok, $text) { if ($ok) { "  OK  $text" } else { "  !!! $text" } }
function Pending { try { ((Invoke-WebRequest "https://api.telegram.org/bot$($env:PA_TEST_TOKEN)/getWebhookInfo" -UseBasicParsing -TimeoutSec 20).Content | ConvertFrom-Json).result.pending_update_count } catch { -1 } }

Add-Type @"
using System; using System.Text; using System.Runtime.InteropServices; using System.Collections.Generic;
public static class Wins2 {
  delegate bool EnumProc(IntPtr h, IntPtr p);
  [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc f, IntPtr p);
  [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  public static List<string> Visible() {
    var list = new List<string>();
    EnumWindows((h, p) => { if (!IsWindowVisible(h)) return true;
      var c = new StringBuilder(256); GetClassName(h, c, 256); var t = new StringBuilder(256); GetWindowText(h, t, 256);
      string cls = c.ToString();
      if (cls == "ConsoleWindowClass" || cls == "CASCADIA_HOSTING_WINDOW_CLASS" || (cls == "#32770" && t.ToString() == "Warning"))
        list.Add(h.ToString() + "|" + cls + "|" + t.ToString());
      return true; }, IntPtr.Zero);
    return list; } }
"@
$before = @{}; foreach ($w in [Wins2]::Visible()) { $before[$w] = 1 }
$seen = @{}
function Watch($sec) { $c = Get-Date; while (((Get-Date) - $c).TotalSeconds -lt $sec) { foreach ($w in [Wins2]::Visible()) { if (-not $before.ContainsKey($w)) { $script:seen[$w] = 1 } }; Start-Sleep -Milliseconds 50 } }

"сообщений боту ждут доставки до запуска: $(Pending)"
"=== запуск ==="
Start-Process $exe
$clock = Get-Date
while (-not (Up) -and ((Get-Date) - $clock).TotalSeconds -lt 60) { Watch 1 }
Mark (Up) ("окно работает, поднялось за {0:N0} с" -f ((Get-Date) - $clock).TotalSeconds)
Watch 30
$p = Pending
Mark ($p -eq 0) "бот забрал ожидавшие сообщения (в очереди: $p)"
$state = (Invoke-WebRequest "http://127.0.0.1:8765/api/state?since=0" -UseBasicParsing).Content | ConvertFrom-Json
Mark ($state.bot.running -and -not $state.bot.trouble) "окно: бот работает (running=$($state.bot.running), беда='$($state.bot.trouble)')"
Mark ($seen.Count -eq 0) "окон консоли за 30 с: $($seen.Count)"
"--- журнал ---"; Get-Content (Join-Path $data "logs\app.log") -Encoding UTF8 -ErrorAction SilentlyContinue | Select-String "бот" | ForEach-Object { "  | $($_.Line)" }

"=== закрываю окно, как крестиком ==="
$main = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine.Contains($data) -and $_.CommandLine -notmatch '--type=' }
foreach ($m in $main) { taskkill /PID $m.ProcessId | Out-Null }
$clock = Get-Date
while ((Get-Process PhoneAgent -ErrorAction SilentlyContinue) -and ((Get-Date) - $clock).TotalSeconds -lt 30) { Watch 0.2 }
Mark (-not (Get-Process PhoneAgent -ErrorAction SilentlyContinue)) ("программа вышла за {0:N1} с" -f ((Get-Date) - $clock).TotalSeconds)
Watch 3
Mark (-not ($seen.Keys | Where-Object { $_ -like "*Warning" })) "окна «Warning» про временную папку нет"
Mark (-not (Get-ChildItem (Join-Path $dir "tmp") -Directory -Filter "_MEI*")) "распаковка удалена"

"=== удаление ==="
& $exe uninstall --yes 2>&1 | ForEach-Object { "    $_" }
Start-Sleep 6
Mark (-not (Test-Path $key)) "записи в «Приложениях» нет"
Mark (-not (Test-Path $data)) "папки с данными (и токеном) нет"
Mark (-not (Test-Path $exe)) "exe нет"
$adbs = Get-CimInstance Win32_Process -Filter "Name='adb.exe'" | Where-Object { $_.ExecutablePath -like "$dir*" }
foreach ($a in $adbs) { Stop-Process -Id $a.ProcessId -Force }
Start-Sleep 1
if ((Test-Path $key) -and ((Get-ItemProperty $key).InstallLocation -like "*\Temp\pa_*")) { Remove-Item $key }
Remove-Item -LiteralPath $dir -Recurse -Force -ErrorAction SilentlyContinue
Mark (-not (Test-Path $dir)) "тестовая папка убрана"
