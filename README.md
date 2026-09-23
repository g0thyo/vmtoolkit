# vmtoolkit

Voicemeeter Potato nag removal for Windows — with a badge. Potato launched through the
toolkit can never show the donationware activation dialog; clicking the Voicemeeter logo
pops a small credit window instead.

No Potato files are modified. Ever. Potato hashes its own executable at startup and uses
that hash to decrypt its UI panels — one changed byte kills the UI. The patch lives in the
running process's memory only; the exe on disk stays byte-identical to factory.

## Quick start

1. Grab `vmactivator.exe` and `runme.bat` from [Releases](../../releases).
2. Run `runme.bat` once (asks for admin). Done.

`runme.bat` copies the exe next to Voicemeeter and points the Start Menu Potato shortcuts
at it. After that the downloaded files can be deleted — Potato just launches nag-free from
the Start Menu like always.

## Why a launcher at all

The one thing that can *not* be done is a permanent on-disk patch: the exe's self-hash is
the decryption key for its own UI resources, so any patched file fails to build its window
and exits silently. In-memory patching per launch is the only approach that survives. The
launcher makes that invisible — click Potato, get Potato.

## Requirements

- Windows 10/11 (x64 or ARM64)
- Voicemeeter Potato **v3.1.1.9** (byte signatures are verified before patching; other
  builds are refused rather than patched blind)

## Python variant

`vm_activator.py` does the same thing for people who want to read or drive it themselves
(stdlib only, Python 3.8+):

```
python vm_activator.py            :: launch Potato x64 nag-free + badge
python vm_activator.py --x86      :: launch the 32-bit build
python vm_activator.py --attach   :: hook an already-running Potato (no restart)
python vm_activator.py --noinject :: kill the nag only, no badge
```

## Building vmactivator.exe from source

No Visual Studio needed — the C# compiler ships with Windows:

```
C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe /platform:x64 /target:winexe ^
  /r:System.Windows.Forms.dll /r:System.Drawing.dll /out:vmactivator.exe src\vmactivator.cs
```

## How it works

The About/Registration nag is one modal `DialogBoxIndirectParamA` call inside a single
launcher function. The toolkit rewrites that call site in memory to jump at an 8-byte
stub: `mov byte [flag], 1; ret`. The dialog never opens; the toolkit sees the flag and
shows the badge. No external API calls in the stub, no injected DLLs, no drivers, no
registry edits. Closing the badge only hides it — every logo click brings it back.

## Notes

- The red "unregistered" banner stays. Potato's normal unregistered flow is what builds
  its UI, so it is deliberately left untouched.
- The trial escalation (`vbDateInst`/`vbCheckInst` in the uninstall key) never reaches a
  dialog, so the 300-second OK-button delay is moot.
- The badge avatar is fetched from GitHub at launch; offline = text-only badge.

## License

MIT — see [LICENSE](LICENSE).
