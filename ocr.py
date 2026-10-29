"""Текст со снимка экрана — встроенным распознаванием Windows (Windows.Media.Ocr).

Зачем: «нужно, чтобы бот дублировал цифрами в чате стату, которая на фото»
(2026-10-29). У TikTok и Instagram цифры есть в дереве экрана, а YouTube Studio
рисует «Просмотры», график и подсказку «27 окт. 2026 г.: 60» на холсте — в
дереве их нет. Модель зрения есть не у всех (на этом ПК — текстовая DeepSeek),
а распознавание Windows стоит в каждой Windows 10/11 и знает языки системы:
живьём 29.10 снимок 1080x2400 прочитан по-русски за секунду.

Без лишних программ: PowerShell 5.1 достаёт WinRT сам. Нет Windows или
распознавателя — None, и цифры YouTube просто не дублируются.
"""
import base64
import json
import os
import subprocess

SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) {
    $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
    $t.Wait(-1) | Out-Null
    $t.Result
}
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync('__PATH__')) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
$out = @()
foreach ($line in $result.Lines) {
    $x1 = 1e9; $y1 = 1e9; $x2 = 0; $y2 = 0
    foreach ($w in $line.Words) {
        $r = $w.BoundingRect
        $x1 = [Math]::Min($x1, $r.X); $y1 = [Math]::Min($y1, $r.Y)
        $x2 = [Math]::Max($x2, $r.X + $r.Width); $y2 = [Math]::Max($y2, $r.Y + $r.Height)
    }
    $out += @{ text = $line.Text; box = @([int]$x1, [int]$y1, [int]$x2, [int]$y2) }
}
ConvertTo-Json -InputObject $out -Compress -Depth 4
"""


def lines(png_path, timeout=60):
    """Строки текста со снимка: [(текст, (x1, y1, x2, y2))] сверху вниз или
    None — распознать нечем."""
    if os.name != "nt" or not os.path.isfile(png_path):
        return None
    script = SCRIPT.replace("__PATH__", os.path.abspath(png_path).replace("'", "''"))
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-EncodedCommand", encoded],
            capture_output=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        data = json.loads(out.stdout.decode("utf-8", "replace").strip() or "null")
    except ValueError:
        return None
    if isinstance(data, dict):                        # одна строка — не список
        data = [data]
    if not isinstance(data, list):
        return None
    got = [(str(d.get("text", "")), tuple(d.get("box") or (0, 0, 0, 0))) for d in data
           if isinstance(d, dict)]
    return sorted(got, key=lambda t: (t[1][1], t[1][0]))
