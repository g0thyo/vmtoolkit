# vmtoolkit

Voicemeeter Potato nag removal for Windows — with a badge. Launch Potato through the
toolkit and the donationware activation dialog can never render; clicking the Voicemeeter
logo pops a small credit window instead.

No files on disk are modified. Ever. Potato hashes its own executable at startup and uses
that hash to decrypt its UI panels — a single changed byte kills the UI. So the patch lives
in the process memory of the running instance only, and the on-disk exe stays
byte-identical to the factory build.

## Requirements

- Windows 10/11 (x64 or ARM64)
- Voicemeeter Potato **v3.1.1.9** installed (byte signatures are verified before patching;
  other builds are refused rather than patched blind)
- Python 3.8+ for the script — or build the self-contained exe (no Python needed)

## Usage — Python

```
python vm_activator.py            :: launch Potato x64 nag-free + badge
python vm_activator.py --x86      :: launch the 32-bit build
python vm_activator.py --attach   :: hook an already-running Potato (no restart)
python vm_activator.py --noinject :: kill the nag only, no badge
```

The script stays resident while Potato runs. Close Potato and it exits with it.

## Usage — standalone exe

```
csc /platform:x64 /target:winexe /r:System.Windows.Forms.dll /r:System.Drawing.dll /out:vmactivator.exe src\vmactivator.cs
```

(csc ships with .NET Framework: `C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe`)

Then run `vmactivator.exe` (x64) or `vmactivator.exe --x86`. Point your Potato shortcuts
at it for a permanent setup.

## How it works

The About/Registration nag is a modal `DialogBoxIndirectParamA` call inside one launcher
function. The toolkit rewrites that call site in memory to jump at an 8-byte stub:
`mov byte [flag], 1; ret`. The dialog never opens; the toolkit polls the flag and shows
the badge. No external API calls in the stub, no injected DLLs, no drivers, no registry
edits. Closing the badge window only hides it — every logo click brings it back.

## Notes

- The red "unregistered" banner stays. Potato keeps its normal unregistered behavior —
  that code path is what decrypts and builds the UI, so it is deliberately left untouched.
- The patch must be reapplied each launch (that's what the launcher is for).
- Trial time-bombs (`vbDateInst`/`vbCheckInst` in the uninstall registry key) never reach
  the dialog, so the 300-second escalation is moot.

## License

MIT — see [LICENSE](LICENSE).
