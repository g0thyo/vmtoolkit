# vmtoolkit

Voicemeeter Potato nag removal (v3.1.1.9, x64+x86) + logo-click badge. Patches memory only — the exe on disk stays factory-clean because Potato self-hashes its file to decrypt its UI.

**Use:** download `vmactivator.exe` + `runme.bat` from [Releases](../../releases), run the bat once (admin), done — delete the folder after.
**Python variant:** `python vm_activator.py [--x86|--attach|--noinject]` (stdlib only).
**Build exe yourself:** `C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe /platform:x64 /target:winexe /r:System.Windows.Forms.dll /r:System.Drawing.dll /out:vmactivator.exe src\vmactivator.cs` — no Visual Studio needed.

Notes: the "unregistered" banner stays (that flow builds the UI, left alone on purpose). Badge avatar fetches from GitHub; offline = text-only. Patch applies per launch — that's what the launcher hides from you.

MIT — [LICENSE](LICENSE).
