# Audio discovery: model and validation protocol

The shipped Audio8 engine is a signal-processing heuristic, not a trained neural
network. Six user-supplied excerpts were used to develop/tune it. They are a
regression set, not an unseen accuracy benchmark. No private audio is committed.
At defaults R=80 / motif=80, the six-direction automatic retrieval check selected
the intended partner for all six excerpts; other combinations failed the active
gates in that tiny collection. This does not establish library-wide precision.

## Online examples collected

`benchmarks/cover_validation_candidates.json` groups the public CoverHunter
Covers80 manifest into 80 musical works / 164 listed recordings. Some groups have
more than two entries. It contains recording labels only. Audio has not been
downloaded and no training has been run. Example groups include A Whiter Shade
of Pale (Annie Lennox / Procol Harum), Abracadabra (Steve Miller Band / Sugar Ray),
and Addicted to Love (Robert Palmer / Tina Turner).
Source: https://github.com/Liu-Feng-deeplearning/CoverHunter/blob/main/data/covers80/dataset.txt

## Candidate pretrained models

- CoverHunter: chunk alignment + CQT-based learned representation, 128-dimensional
  embeddings. Authors provide an SHS100K-trained checkpoint and inference code.
  https://github.com/Liu-Feng-deeplearning/CoverHunter
- Discogs-VINet: musical version identification; pretrained weights and chunk
  inference. Match code/config/checkpoint versions as documented by the author.
  https://github.com/raraz15/Discogs-VINet
- SHS100K official 2025 metadata: work/performance labels, with composition-disjoint
  train/validation/test partitions. Metadata is not downloaded audio.
  https://github.com/second-hand-songs/shs-100k

These models target versions of compositions. A shared drum groove between
otherwise unrelated compositions is a separate objective: retain a separate
rhythm channel and obtain explicit groove labels before claiming it is learned.

## Required experiment before integration

1. Evaluate the original pretrained checkpoint on external, unseen works. Keep all
   versions of a composition in one split. Keep the six development excerpts out
   of claimed held-out accuracy.
2. Use local phrase/chunk embeddings, not only whole-track averages. Compare local
   matches after offsets, key shifts and moderate tempo/arrangement variation.
3. Include hard negatives: similar BPM, same key/chords, same genre, common kick
   pattern, different phrase. Do not equate a work mismatch with no shared groove.
4. Report recall@1/@10 and false suggestions above the chosen threshold, along with
   CPU scan time, query latency and index size on Windows.
5. If needed, fine-tune contrastively on positive/negative pairs with tempo/pitch,
   gain, EQ and crop augmentation. Evaluate at work level, not randomly split clips.
6. Export and validate a pinned CPU inference model (e.g. ONNX) and retain model
   hashes/version in the cache. Do not silently replace the heuristic or call a
   model download successful when weights are absent.

Runtime remains automatic: index library once, add unseen current tracks locally,
query the index, route accepted audio matches to slot 5 and LIVE 10. Original
slots 1–4 stay unchanged; bans/played/duplicate exclusions apply to discovery.
