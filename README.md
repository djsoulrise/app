# DJ Miljan Assistant Lite

Separate lightweight edition. Heavy Audio14 remains on the main branch.

- Original deterministic five recommendations (unchanged scoring, gates and selection).
- Three extra candidates from the same scoring pass; these are NOT audio matches.
- Three PRE and three POSLE neighbors from imported Traktor lists and enabled learning imports.
- Keyboard-only shortcuts, Ctrl+X native window restore, font scaling, compact console.
- Read-only Traktor NML, playlist/history learning, events, requests, M3U8 and song export.
- Metadata-only duplicate detection and Traktor grid inspection.
- No MIDI ports, controller learning, ONNX models, audio decoding, deep/live scans,
  scan workers, neural similarity, cloud AI or Gig mode.

The existing database and settings location is retained. Existing audio tables are
ignored, not deleted. Close other Assistant versions before launching Lite.

If you created an additional DJ Assistant / Generic MIDI mapping inside Traktor,
disable its In-Port and Out-Port there. Removing MIDI from this app does not alter
your Traktor mapping. Preserve your original controller mapping.

Export creates a writable destination. A stale path under another Windows user's
profile is replaced with the current Windows Documents export folder, falling
back to the app data Exports folder if necessary. The destination is saved in
Settings and reported after export. Music originals are only read/copied.

Build: `python -m pip install -r requirements.txt pywebview pyinstaller`
then `python -m PyInstaller --noconfirm "DJ asistent.spec"`.
Windows CI checks the original five against the pre-audio implementation, compact
UI, keyboard learning, day exports, and native minimize/restore in source and EXE.
