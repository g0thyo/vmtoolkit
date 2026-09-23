# vmtoolkit

Voicemeeter Potato, without the activation nag. Works on v3.1.1.9 (x64 and x86).

Nothing on disk is modified — the patch lives in the running process only.
That's required, not a choice: Potato hashes its own exe to decrypt its UI,
so a patched file won't open. In-memory is the only way that works.

## Install

1. Download `vmactivator.exe` and `runme.bat` from [Releases](../../releases).
2. Run `runme.bat` once (it asks for admin).

Done. The Start Menu Potato shortcuts now launch nag-free, to check click the
Voicemeeter logo on the top left, it should show a small credit badge. enjoy :3 

## Source

Everything is in this repo:

- `vm_activator.py` — the Python version (stdlib only, Python 3.8+)
- `src/vmactivator.cs` — the exe's source

## Build from source

No Visual Studio needed — the compiler ships with Windows:

```
C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe /platform:x64 /target:winexe /r:System.Windows.Forms.dll /r:System.Drawing.dll /out:vmactivator.exe src\vmactivator.cs
```

## Notes

- The "unregistered" banner stays — that code path builds the UI, so it's left alone.
- The badge avatar loads from GitHub; offline you get a text-only badge.
- Run Potato outside the toolkit and you get plain unpatched Potato — no harm, just the nag again.

MIT — [LICENSE](LICENSE)

---

This script is intended for personal use only. If you use Voicemeeter for work or make money from it, consider paying what you can on the [Voicemeeter site](https://vb-audio.com/Voicemeeter/potato.htm).

