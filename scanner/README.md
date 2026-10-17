# DJ Miljan Audio Scanner v1

Standalone offline Windows x64 scanner. It uses Essentia.js 0.1.3/WebAssembly,
not a newly trained neural network. Node and FFmpeg are bundled; WSL, Ubuntu,
Python and a separate Node installation are not required by the user.
The UI requires WebView2, as does DJ Miljan Assistant Lite.

## Workflow

1. Add music folders or optionally select a read-only Traktor collection.nml.
2. Choose workers (balanced defaults to up to four) or maximum CPU mode.
   CPU and available RAM cap the actual worker count. Balanced native workers
   use below-normal priority. Maximum mode allows normal priority/all logical
   cores, subject to RAM limits. One worker is the low-load mode.
3. Click **Skeniraj / nastavi**. Unchanged valid records are skipped. Failed
   unchanged files are skipped unless retry is explicitly enabled. Broken files
   do not stop the collection. Stop keeps completed results; Pause lets active
   tasks finish and stops new dispatch.
4. Click **Pronadji parove**. This is a separate, resumable offline job. Cached
   pairs are skipped. Up to 100 tracks use every candidate pair. Larger libraries
   use an approximate audio-only segment index (40 candidates per song, or up
   to 100 in thorough mode), followed by local verification.
5. Inspect results, intervals and previews. Thresholds filter saved results.
   Scores are indices, not probabilities or percentages of certainty. A
   shuffled-order control and minimum duration remain mandatory. Good/bad votes
   are stored as feedback, not used to claim automatic model training. Bad votes
   are excluded from exported Assistant results.
6. **Izvezi parove za Assistant** writes a compact `.djmatches` package.
   **Izvezi punu skeniranu bazu** writes `.djscan` with features and cached pairs.
   Neither package contains original audio. CSV export writes separate scanned
   file/error and pair reports. The UI prints the exact export location.
7. In the supplied **Lite AudioDB** version: Opcije -> Podesavanja -> Tri dodatna
   predloga desno -> Uvezi audio rezultate. After import, choose Uvezena audio
   baza and save. The original five left recommendations are unchanged. PRE and
   POSLE remain based on Traktor lists/history. Without accepted results, the
   three audio slots stay empty; no random fallback is added.

## Data and portability

Scanner database: `%APPDATA%\DJ Miljan Audio Scanner\scanner.db`.
Exports: `%APPDATA%\DJ Miljan Audio Scanner\Exports`.
Assistant database remains `%APPDATA%\DJ asistent\library.db`.
All three paths are also shown in their respective UI.

To move a scan to another PC, add the destination music folders, import the full
`.djscan` package and use Relocate. SHA256 verifies actual file bytes. A matching
name alone is never sufficient. Same audio with different tags/encoding can have
a different hash; that version needs analysis. Matching ambiguous duplicates are
not silently relocated. The Lite import verifies local content once by reading
file bytes; it does not decode audio. Live recommendations only read cached
relations and inexpensive file metadata. Reimport after moving/changing files.

## Audio method and limitations

Independent descriptors cover tonal sequences (HPCP + transposition-invariant
chroma cross-similarity), multi-band onset/accent patterns, and locally whitened
log-frequency texture. Essentia local sequence alignment verifies candidates;
a block-shuffled control rejects generic/common patterns. Metadata, folders,
comments, BPM and global key do not filter the audio search.

The complete song is represented and overlapping windows are compared. This
version does not identify a semantic chorus or read Traktor waveform caches/CUE
hints. Window matching covers choruses and shorter fragments without requiring a
manual pair choice. Very short/shared drum fragments remain difficult.

Source clip validation found four accepted relationships among 11 supplied
excerpts: Dado/Xristos, Tambov/Chiki, Gia/Muska lutka and Loco/Poznata.
Alex Roll/Rasputin did not pass reliable verification and is not auto-exported as
an accepted relationship. This is an explicit remaining failure, not a hidden
manual exception. There is no promise of finding every connection or of zero
false positives in a full collection. Small development examples are not an
independent benchmark; see VALIDATION.md.

## Build and tests

From the repository root, on Windows Python 3.12:

```
python -m pip install -r scanner/requirements.txt -r requirements.txt pytest pyinstaller
npm ci --prefix scanner --ignore-scripts
python scanner/build_runtime.py
python -m pytest tests scanner/tests -q
python -m PyInstaller --noconfirm --clean scanner.spec
python -m PyInstaller --noconfirm --clean "DJ asistent.spec"
```

`scanner/build_runtime.py` verifies Node 24.19.0 Windows x64 against a pinned
SHA256 and the official checksum list. Runtime dependencies are built into the
EXE. The GitHub workflow verifies real packaged decoder/WASM workers, broken
file skipping, resume, full export/import, Lite without heavy modules, compact
UI and native Lite window restoration.

Corresponding scanner source is AGPL-3.0-or-later and is in this folder, plus
`scanner_desktop.py`, `scanner.spec` and the scanner workflow. Unmodified
Essentia.js source/build information and dependency notices are listed in
THIRD-PARTY-NOTICES.txt. The main and lite branches remain separate baselines.
