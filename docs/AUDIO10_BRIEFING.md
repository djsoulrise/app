# Audio 10: shared music, not generic genre similarity

## Failure reproduced and code evidence

The requested relationship is shared or recognizably similar musical material:
a melody, riff, accompaniment, rhythmic phrase, or reused fragment, despite
different lyrics, language, singer, tempo, key, or arrangement. A generic disco
or house beat is not sufficient evidence.

The Audio 9 screenshots expose a precision problem in the right LIVE list.
They do not establish that the left original four recommendations are wrong.
In the Beautiful example the expected relationship is Kafe Espresso, not the
different Dado track named in a suggestion. Audio 9 was tested on short clips;
its reported success did not establish accuracy in a much larger collection.

At commit 988fc3b, `app/auto_audio.py` had these weaknesses:

* `select_sections` retained only two 24-second candidates. A shared part
  elsewhere in either complete recording could be absent from the index.
* `rank` independently maximized a four-beat, three-band rhythm profile,
  chroma alignment and learned-vector cosine across section pairs. They
  could describe different source/target intervals.
* The learned representation padded a short excerpt to the published model's
  much longer input context. This is not evidence of a shared short sample.
* `passing` accepted rhythm OR motif, including a learned cosine >=75 with a
  weak phrase check >=70. The local verifier allowed arbitrary gap walks.
* `fifth` blended those measurements, and could prefer generic high scores.
  The near-best learned-score test was calculated before eligibility and
  duplicate exclusions, so a better excluded item could suppress a useful
  eligible one. Raw embedding cosine was presented too prominently.
* The previous whole-track `audio_rhythm.fifth` engine was no longer called:
  failure of the new index went directly to the original fifth suggestion.

Folder, Comment tags, key and original style gates still influence the
original four via `app/suggest.py`. Those calculations were not modified.
The audio discovery pool still honors explicit bans, already played songs,
current-track/title identity and the first four selections. Whole-library
audio discovery can bypass style/key gates as before.

## Implemented changes

`app/phrase_match.py` stores compressed harmonic CQT pitch-class timelines
from the whole recording, not just two guessed choruses. Two frequency
ranges help tolerate different bass/lead arrangements. Rolling-window
retrieval searches all source and target positions. It then verifies a
bounded surrounding interval using stacked note context, mutual cross-
recurrence and slope-constrained local alignment, across all 12 pitch shifts.
Four reproducible controls rearrange two-second blocks. Duration, match
density, alignment strength and contrast against those controls must pass.
This is a heuristic musical-phrase score, not a probability.

`app/sample_match.py` adds spectral-peak pair fingerprints for a short copied
fragment. It requires many distinct anchors with a consistent location,
pitch displacement and timing; a few accidental shared peaks cannot pass.
This branch is complementary to musical-version matching: it is more
specific to reused recordings and will not recognize every re-recording.

The existing pretrained Discogs-VINet network helps retrieve candidates.
It does not make the final shared-phrase decision. There are no song-name
overrides, manually linked pairs or per-song thresholds. No new neural
network was trained for this revision. ONNX Runtime telemetry is disabled
before initialization as well as through its runtime API.

Up to 48 phrase, 24 learned and 24 fingerprint candidates are verified;
their union can be smaller. Retrieval remains approximate: a genuine match
outside that shortlist can still be missed. Verified results can appear
while the remaining candidates are being checked.

The fifth slot and LIVE use this ordered policy:

1. Verified shared musical phrase or reused audio fragment.
2. If none passes, the existing saved whole-track rhythm engine, with its
   own saved threshold and quality gates.
3. If neither passes, retain the original deterministic fifth suggestion.

LIVE now shows up to three accepted results from the first successful audio
level, followed by three observed predecessors and three observed successors.
The six history slots use all imported Traktor History lists and the app
played log, including short History lists disabled for original learning.
Ordinary curated or generated playlists are not treated as proof of a play.
History uses counts of direct adjacency, with source names available, not
AI similarity or genre filters. History has a separate muted blue color and
PRE / POSLE labels. Duplicate identities within the right panel are removed. It does not mix weaker fallback levels into a confirmed phrase list,
and it never fills the right-hand list with normal recommendations.

Audio ritam Start now maintains both indices from one decoded recording.
Previously valid whole-track results are reused. Live scan has a separate
explicit ON/OFF switch and defaults to OFF, even if an older version saved
auto_scan=true. OFF prevents audio decoding/analysis for an uncached loaded
track; cached retrieval and history continue. The full-library Start/Stop
controls remain independent. Switching OFF cancels an active live scan. The new phrase-index
version is 3; its whole-song descriptors require one new scan. Stop and
resumption preserve completed tracks. NML, TSI and original audio are not
written. Cue hints still help the learned section index, but do not bound
the whole-song phrase or fingerprint search.

## Actual validation and limitations

Validation used all ten supplied excerpts mixed with ten actual official
Apple preview recordings, including several artists/titles seen among the
unwanted suggestions. These previews are not the user's exact local edits;
therefore they cannot prove how those full local files will rank.

At default thresholds, the four clear musical-version pairs were retrieved
in both directions: Beautiful/Kafe Espresso (supplied clips), Chiki/Tambov,
Gia/Muska Lutka and Poznata/Loco. The independently retrieved official Kafe
Espresso preview also passed for Beautiful. The tested unrelated previews
were not accepted as shared music for those ten queries. This is a small,
development-time validation set, not an unseen or production accuracy rate.

The supplied Rasputin/Alex excerpts remain a known recall failure at the
strict default thresholds. Neither the new phrase verifier nor the short
sample fingerprints establish that relationship confidently in those two
clips. The implementation deliberately does not lower a special threshold
or insert a named exception to claim success. Recognition in the user's
complete files is unverified; whole-song indexing addresses missing-section
coverage, but cannot be claimed to solve that example without testing the
actual matching material. Source separation or a validated partial-version
model may be needed for heavily overlaid or re-performed accompaniment.

Synthetic regression checks cover pitch displacement, time offsets,
fragments outside the first section, shuffled note order, static chords,
copied short fragments, unrelated audio, cancellation, and the three-level
fallback. A separate regression verifies that the original first four are
unchanged while audio discovery can select outside key/tempo gates.

## Evening exports and removed UI

`app/event_exports.py` exports each local calendar day within an event,
in play order. M3U8 export keeps repeated plays; song export copies each
physical file once into a new dated folder with collision-safe filenames
and a portable relative-path M3U8. Missing files are listed. Originals are
preserved, existing exports are not overwritten, and cancelled partial
copies are removed. Viewing a past event no longer activates it implicitly.

Tag management, AI tagging and tag-editing controls were removed from the
UI. Imported tags and read-only filtering remain because the original
recommendation algorithm still uses them.

## Primary references

* MTG Essentia: https://essentia.upf.edu/tutorial_similarity_cover.html
  (HPCP, stacked cross recurrence, pitch invariance, local alignment).
* CoverHunter: https://github.com/Liu-Feng-deeplearning/CoverHunter
  (learned retrieval and aligned musical chunks).
* Discogs-VINet: https://github.com/raraz15/Discogs-VINet
  (published musical-version embedding model; weights unchanged here).
* Dan Ellis audfprint: https://github.com/dpwe/audfprint
  (consistent landmark time offsets identify a fragment within a recording).
* ONNX Runtime privacy controls:
  https://github.com/microsoft/onnxruntime/blob/main/docs/Privacy.md

## Audio 11 experimental periodic-context branch

The short drum-pattern experiment is now integrated as an additional, explicitly
experimental first-level audio criterion. The first four original suggestions
remain unchanged. Existing melodic and sample verification are retained.
`app/loop_audio.py` repeats each 24-second CQT in the pretrained network's
fixed context instead of zero padding. Windows cover the entire file every
12 seconds, including the first and last window. Each window also has a
seeded one-second-block shuffled control. Both cross-control directions use
the same window search budget. A candidate requires cosine >=62 and at least
four score points above the strongest shuffled control; settings expose the
cutoff. This is a heuristic evidence gate, not calibrated probability or proof
of a copied sample, and no new network was trained.

The actual application integration on ten provided recordings plus ten public
preview distractors now accepts Rasputin/Alex in both directions (68.975 versus
control54.248), preserving the other verified pairs. Large-library precision
is not established. Periodic signatures use cache version4; a full rescan is
required. Whole-track rhythm cache remains reusable. The library Start/Stop
and default OFF live-scan switch remain independent.
