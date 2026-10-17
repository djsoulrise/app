# Audio discovery: model and validation protocol

Audio9 includes a published, pretrained Discogs-VINet ISMIR2024 neural model,
converted to CPU ONNX. No new neural network was trained on the user's six clips.
They remain a development/regression set, not an unseen accuracy benchmark.
The original signal-processing rhythm/phrase checks supplement the neural index.
No private audio or external preview audio is committed.

## Online examples collected

`benchmarks/cover_validation_candidates.json` groups the public CoverHunter
Covers80 manifest into 80 musical works / 164 listed recordings. Some groups have
more than two entries. It contains recording labels only. This manifest itself is metadata only. A separate local check downloaded 13
public Apple audio previews for five complete pairs and three distractors. No
additional neural training was run. Example groups include A Whiter Shade
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

## Validation and future fine-tuning

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
