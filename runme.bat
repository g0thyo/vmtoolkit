@echo off
:: vmtoolkit one-time installer — run once, keep the exe, delete the repo.
:: Copies vmactivator.exe next to Voicemeeter and repoints the Start Menu
:: Potato shortcuts at it. Nothing else on the system is touched.
setlocal
set "DST=C:\Program Files (x86)\VB\Voicemeeter"

net session >nul 2>&1
if errorlevel 1 (
    echo Requesting administrator rights...
    powershell -NoProfile -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

if not exist "%~dp0vmactivator.exe" (
    echo vmactivator.exe not found next to this .bat — download it from Releases first.
    pause
    exit /b 1
)
if not exist "%DST%\voicemeeter8x64.exe" (
    echo Voicemeeter Potato not found at "%DST%" — is it installed?
    pause
    exit /b 1
)

copy /y "%~dp0vmactivator.exe" "%DST%\vmactivator.exe" >nul

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$exe = '%DST%\vmactivator.exe';" ^
  "$dir = 'C:\ProgramData\Microsoft\Windows\Start Menu\Programs\VB Audio\VoiceMeeter';" ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "foreach ($lnk in @(\"$dir\Voicemeeter Potato.LNK\", \"$dir\Voicemeeter Potato x64.LNK\")) {" ^
  "  if (Test-Path $lnk) {" ^
  "    $s = $ws.CreateShortcut($lnk);" ^
  "    $s.TargetPath = $exe;" ^
  "    $s.Arguments = $(if ($lnk -notmatch 'x64') { '--x86' } else { '' });" ^
  "    $s.WorkingDirectory = '%DST%';" ^
  "    $s.Save()" ^
  "  }" ^
  "}"

echo.
echo Done. Start Menu -^> "Voicemeeter Potato" now launches nag-free.
pause
