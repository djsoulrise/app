# Audio 12: list import, portable cache and scan acceleration

LIVE headings are PRE and POSLE. The Playlists page imports NML/M3U/M3U8
lists into the unchanged original transition-learning algorithm. Explicitly
imported short lists also qualify. Additional imports contribute to PRE/POSLE
while their learning checkbox is enabled; all previously imported Traktor
lists still count, and Assistant event plays remain excluded. Exact paths
match first; a unique exact filename can resolve a stale/relative path.
Ambiguous and missing tracks are reported, positional gaps retained, source
files never changed, and reimport updates existing lists without duplication.

## Performance

A warmed Linux CPU profile of a 240-second test signal measured section
selection, phrase/landmark extraction and both neural signature passes at
6.595 seconds. A frequency median filter consumed 4.870 seconds. Replacing
it with bounded 64-row sliding views plus numpy.partition measured the same
pipeline at 3.451 seconds. Symmetric padding exactly reproduces the original
scipy size=(1,31) reflect-boundary median. Tests compare bit-exact results
including short dimensions, ties, block edges and unchanged landmark bytes.
Decoder and legacy whole-track rhythm work are outside the measurement; this
is not a promised Windows/library-wide speed factor. No thresholds, analyzed
windows, model weights or cache versions change. Audio11 scans remain valid.

## Transfer and validation

Audio rhythm provides Export audio database, Import audio database, Relocate
and Check scanned (without scan). Actual database and transfer paths appear
locally. Frozen default: %APPDATA%\DJ asistent\library.db. DJ_DATA_DIR may
override this. Export creates a gzip JSON-record .djaudio file with valid numeric
signatures and SHA256 file identities, not music or the entire SQL database.
Import the target collection first, then import the package. Exact file
content matches regardless of local IDs, paths and modification timestamps.
Changed tags change file SHA256; altered files are not guessed by name.

Relocate searches explicitly provided roots, verifies SHA256, and may repair
an unambiguous missing path in the Assistant database only. It never writes
Traktor files. Incompatible engine/model versions are skipped and counted;
Relocate cannot convert them. Source analyzed window offsets are adopted
because file content is identical; source cue hints are retained as provenance
and the target cue-key stamps later local cue changes. Hashes are stat-cached
and changes during hashing or application are detected. Export/import show
progress and Stop. Check reads cache metadata and file stats only, with no
decoding, hashing or inference; it lists status and paths without a scan.

Local tests cover changed-path/ID/timestamp roundtrips, wrong SHA despite same
names, model incompatibility, malformed versions, Relocate, cancellation,
local-only read/check APIs, list formats, reimports and ambiguous/missing
tracks. A real Rasputin/Alex transfer into a separate database with copied
files and new IDs retained score68.975/control54.248 exactly. Windows browser
tests exercise the new file pickers, import/export/check controls and labels.
Frozen EXE/model and native WebView restore tests are still required.
