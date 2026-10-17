# Audio9: actual model validation

Model: Discogs-VINet ISMIR2024, by R. Oguz Araz, Xavier Serra and Dmitry Bogdanov.
Published weights; no private-example fine-tuning. MIT license is bundled.
Pinned source: `3029d61c6f8e6ef9991acd2f0e4743e748134d9a`.
Checkpoint SHA256: `9d0b1d5cdd459a22b1062f69f0ef12ff59d0681a5d90d338827598dc55e8bf13`.

## Real inference, not title matching

CQT magnitude (84 bins, 22050 Hz, hop 512), author float16 round trip,
zero-pad to 8000 frames, mean time reduction 20x, scale, fixed CPU ONNX input
1x1x84x400, normalized 512-dimensional embeddings. FFmpeg supplies mono PCM;
the model receives no title, artist, folder, Comment, key or genre.

The exported ONNX outputs matched the original PyTorch outputs on three random
inputs to a maximum absolute error below 0.000001. The build reruns parity
validation and the frozen EXE executes actual CQT + CPU inference as a self-test.

## Six supplied excerpts

Whole-excerpt embeddings missed the Rasputin / Alex Roll relationship. Searching
24-second local windows fixed the rankings: the expected partner ranked first
for each of the six query directions. Approximate pretrained cosine similarities:

- Chiki Chiki Ta / Tambov: 0.838
- Rasputin / Alex Roll: 0.796
- Xristos / Dado: 0.795

These are similarities, not probabilities or proof of copying. The production
index uses two audio/cue-selected phrase regions, with offsets -6/0/+6 seconds
clamped to valid audio, and verifies candidates against ordered local chroma.
All six expected pairings also passed the integrated retrieval/regression check.
The examples helped develop the system, so this is not a held-out accuracy claim.

## Four later examples and a discovered ranking fault

With unchanged initial defaults, Gia / Muska lutka worked but Poznata / Loco
missed. The ten-clip combined check initially selected expected partners in only
7/10 directions. A legacy phrase score also outranked a stronger learned match.
This finding prompted the final confidence/ranking change; do not describe these
four clips as held-out after that change.

The final defaults use a 75-point learned cutoff plus local phrase evidence.
Borderline neural matches below 85 must be within 2.5 points of the query's best
neural candidate across the full index, before exclusions. Generic phrase scores
cannot override weak neural evidence unless the local phrase score is at least
90. Accepted results combine audio evidence (65%) and learned similarity (35%).
Rhythm-only mode keeps its own ordering. The repeated ten-direction check then
ranked all five expected pairs first in both directions. Rhythm-only alternatives
can still appear in LIVE; they are labelled separately, not asserted to be covers.

## Additional online audio actually tested

Covers80 work labels came from the public CoverHunter manifest. Exact artist/title
lookups on Apple's public Search API supplied 13 audio previews: five complete
pairs (10 paired queries) and three extra distractors. No audio is redistributed.

Pretrained local-window nearest-neighbor retrieval ranked the counterpart first
in 8/10 paired query directions. Successful pairs: A Whiter Shade of Pale
(Annie Lennox / Procol Harum), Abracadabra (Steve Miller Band / Sugar Ray),
Addicted to Love (Robert Palmer / Tina Turner), Before You Accuse Me
(Creedence Clearwater Revival / Eric Clapton). Both All Along the Watchtower
queries (Bob Dylan / Dave Matthews Band) missed first place. Preview sections
need not correspond, and training-composition overlap has not been audited.
This is a small smoke evaluation, not a general accuracy percentage.

## Runtime and acceptance

Model/signatures are versioned and cached locally. New collections need indexing,
not retraining. No cloud inference or runtime model download is required. The
first call includes initialization; warmed local CPU feature/inference calls on
short excerpts were typically under one second in this environment. This is not
a benchmark of the user's laptop.

Up to 120 learned candidates and 120 ordered-chroma candidates are locally
verified. Original rhythm and phrase passes remain independent. A strong learned
match can support a motif pass only with meaningful ordered-phrase alignment;
a high embedding score alone cannot override failed evidence. Thresholds and
model use are adjustable. Slots 1–4 remain the original recommender; slot 5 and
LIVE 10 share audio eligibility, bans, played-track and identity exclusions.

Future work: larger composition-disjoint tests and hard groove negatives before
any further fine-tuning. Models for cover versions do not by themselves establish
that unrelated compositions share a drum groove.

## Mixed-collection regression

The final integrated engine was also run with all ten supplied excerpts plus the
13 downloaded public previews in one temporary 23-recording collection. Each
of the ten supplied queries ranked its expected partner first. No titles or
artist labels entered feature extraction or model inference. This remains a
small development/regression check, not a proof of universal generalization.
