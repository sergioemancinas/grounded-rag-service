# Evaluation

## Why there are two harnesses, and only one of them is evidence

`scripts/eval_golden.py` is a **smoke test**, not an evaluation. It runs eight
questions over the three fictional Acme Storefront documents and checks the
pipeline still answers with citations. Its retrieval numbers are structurally
incapable of failing: the corpus has 3 documents and 18 chunks, and context
selection returns chunks from all three documents for every question, so
"was the expected document retrieved" is always true. It reports 100% and
that number means nothing. It is kept because a fast end-to-end check that
needs no network is genuinely useful, not because it measures quality.

`scripts/eval_beir.py` is the evaluation. It runs on a public benchmark with
published baselines, so the numbers can be checked by anyone and can go down.

## Results

BEIR SciFact, 300 test queries, 5,183 documents. Measured with the repo's own
`tokenize`, BM25 (k1=1.2, b=0.75), `cosine_similarity`, and
`reciprocal_rank_fusion` (constant 60), candidate pool 100 per lane. Two
embedders are reported side by side: the offline default and a real ONNX
model. BM25 is identical in both tables (lexical only).

### Offline default (`LocalHashEmbedder`)

```bash
python scripts/eval_beir.py --lanes bm25,dense,hybrid
```

| Lane | nDCG@10 | Recall@10 | MRR@10 | Published BM25 reference |
| --- | --- | --- | --- | --- |
| `bm25_only` | **0.6047** | 0.7262 | 0.5697 | 0.665 |
| `dense_only` | 0.1557 | 0.2381 | 0.1322 | — |
| `hybrid_rrf` | 0.3756 | 0.5361 | 0.3322 | — |

### Real embedder (`BAAI/bge-small-en-v1.5` via fastembed)

```bash
pip install -e ".[eval]"
python scripts/eval_beir.py --lanes bm25,dense,hybrid \
  --embedder examples.custom_embedder_fastembed:FastEmbedEmbedder
```

Corpus embeddings are cached under `data/benchmarks/` keyed by embedder class
and model name so a rerun does not re-embed 5,183 documents.

| Lane | nDCG@10 | Recall@10 | MRR@10 | Published BM25 reference |
| --- | --- | --- | --- | --- |
| `bm25_only` | 0.6047 | 0.7262 | 0.5697 | 0.665 |
| `dense_only` | **0.7200** | 0.8452 | 0.6845 | — |
| `hybrid_rrf` | 0.6783 | 0.7783 | 0.6528 | — |

The published reference is SciFact BM25 nDCG@10 = 0.665 from Table 2 of
[Thakur et al., BEIR (NeurIPS 2021)](https://arxiv.org/abs/2104.08663). It is
a **different implementation** (Elasticsearch, separate title and text
fields) and is shown for orientation only. This is not a reproduction of that
number, and the ~0.06 gap is consistent with single-field indexing and a
simpler tokenizer.

## The finding: hybrid quality is an embedder property

With the default offline configuration, fusing the dense lane into BM25 makes
retrieval **substantially worse**: 0.3756 against 0.6047, a 38% relative drop
in nDCG@10.

The cause is not the fusion. It is the default embedder. `LocalHashEmbedder`
is a signed hashing trick that buckets tokens into 256 dimensions; it is
deterministic and dependency-free, which is what makes the zero-credential
quickstart possible, but it carries almost no semantic signal. Its standalone
nDCG@10 of 0.1557 is the direct measurement of that. Reciprocal rank fusion
then does exactly what it is designed to do: it lets a lane with real signal
be pulled down by a lane with almost none.

With `BAAI/bge-small-en-v1.5` (same RRF constant 60, no parameter tuning on
this split), the picture reverses against BM25 and stays honest about dense:

- Dense alone reaches **0.7200** nDCG@10.
- Hybrid reaches **0.6783**, beating BM25 (0.6047) by about 12% relative, but
  still trailing dense-only. RRF is again doing its job: it blends a stronger
  dense ranking with a weaker lexical one, so the fused list sits between
  them rather than above both.

Two honest consequences:

1. **The README's architectural claim needs a qualifier.** Hybrid retrieval
   is the right design *when the dense lane uses a real embedding model* and
   you care about identifier-heavy queries that pure dense can miss. On this
   SciFact split, untuned RRF hybrid beats BM25 and loses to dense alone; with
   the offline default it is a net negative. The repo publishes both
   measurements rather than quoting only the flattering one.
2. **This is why the fictional corpus was abandoned as an evaluation.** Three
   documents cannot expose a 38% retrieval regression. A public benchmark
   found it in one run.

## What is gated in CI

The `benchmark` job runs the BM25 lane against SciFact with
`--threshold 0.58` and fails the build below it. That gate is deterministic:
stdlib scoring, no model download, no API key, no LLM judge, so it cannot
flap. Metrics are uploaded as a build artifact.

Dense and hybrid lanes are not gated, because their scores are a property of
whichever embedder is configured rather than of the retrieval code. The
fastembed numbers above require the optional `eval` extra
(`pip install -e ".[eval]"`).

## Metrics

Standard definitions, implemented in `scripts/eval_beir.py` and unit-tested
against hand-computed fixtures in `tests/test_eval_beir.py`:

- **nDCG@10** — gains `2^rel - 1`, log2 rank discount, normalized by the
  ideal DCG from the sorted relevance labels. BEIR's primary metric, chosen
  there because it handles binary and graded judgements alike.
- **Recall@10** — fraction of a query's relevant documents appearing in the
  top 10.
- **MRR@10** — reciprocal rank of the first relevant hit, 0 if none in 10.

## Dataset and licensing

BEIR SciFact, fetched from the
[UKP mirror](https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scifact.zip)
(2.8 MB) into `data/benchmarks/`, which is gitignored: the corpus is never
committed. Claims are CC BY 4.0 from
[allenai/scifact](https://github.com/allenai/scifact); abstracts are ODC-By
1.0. Both are compatible with this MIT repository.

Other BEIR subsets were considered and rejected: NFCorpus is restricted to
academic use by its
[source terms](https://www.cl.uni-heidelberg.de/statnlpgroup/nfcorpus/), which
is incompatible with a public portfolio repository; TREC-COVID (74 MB) and
SCIDOCS (142 MB) are too large to be worth their evaluative value here.

## Limitations

These matter as much as the numbers.

- **One dataset.** SciFact was chosen for licence cleanliness and CI cost, not
  because it flatters the results. One dataset is weak evidence of
  generalization, and a second would strengthen it.
- **SciFact is claim verification, not question answering.** Queries are short
  declarative scientific claims. It exercises retrieval well and is a poor
  proxy for the conversational `/v1/ask` surface.
- **BEIR is no longer cleanly zero-shot.** Its corpora now appear in the
  training data of many embedding models, which is why
  [RTEB](https://github.com/embedding-benchmark/rteb) and BRIGHT exist. Absolute
  numbers here are comparable to the published BM25 baseline but should not
  be read as a leaderboard position.
- **Generation and grounding are not evaluated.** These numbers cover
  retrieval only. The grounding gate's score is deliberately absent from every
  results table: the offline generator copies retrieved chunks verbatim, so
  the judge scores text against itself and always returns ~1.0. That is a
  tautology, not a measurement.
- **Cross-implementation comparison is orientation, not reproduction.** See
  the note under the results table.
- **Untuned fusion.** RRF constant 60 is the common default, chosen before
  seeing the fastembed numbers. Retuning it on this test split would be a
  separate labelled experiment.

## What would strengthen this

In rough order of value: a second licence-clean dataset; a citation-attribution
metric checking that each `[n]` marker points at a chunk that actually
supports its sentence, which needs an LLM judge and therefore belongs in a
scheduled run publishing a dated artifact, not in the CI gate; and a labelled
fusion-parameter study that discloses any post-hoc choice of RRF constant.
