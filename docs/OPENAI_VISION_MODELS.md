# OpenAI Vision Models for AI Autograder

Models that support **vision** (image input) for grading Jupyter notebooks with plots. Run `python test_openai_connection.py --list-models` to see what your API key can use.

---

## Models Available to Your API Key

From `python test_openai_connection.py --list-models`:

| Model | Vision | Notes |
|-------|--------|-------|
| gpt-4.1-2025-04-14 | Yes | Dated snapshot |
| gpt-4.1 | Yes | Flagship GPT-4.1 |
| gpt-4.1-mini-2025-04-14 | Yes | Dated snapshot |
| gpt-4.1-mini | Yes | Cost-effective |
| gpt-5 | Yes | Flagship GPT-5 |
| gpt-5-mini-2025-08-07 | Yes | Dated snapshot |
| gpt-5-mini | Yes | Low cost; high variance (temp=1) |
| gpt-5-nano-2025-08-07 | Yes | Dated snapshot |
| gpt-5.2 | Yes | GPT-5.2 series |
| gpt-3.5-turbo-16k | **No** | Text only, no vision |

---

## Temperature (deterministic grading)

Implementation: `temperature_for_model()` in `llm/client.py` returns **1.0** for any model
name starting with `gpt-5` (including `gpt-5.2`, `gpt-5-mini`, `gpt-5-nano`, dated
snapshots), and **0.0** otherwise (e.g. `gpt-4.1`, `gpt-4.1-mini`, `gpt-4o-mini`).

- **GPT-5 family**: API only allows temperature=1 for these models; regrades can vary a lot.
- **GPT-4.1 / GPT-4.1-mini / GPT-4o-mini**: temperature=0 is used for reproducible grading.

### HW1 multi-model variance study (Mar 2026)

Course HW1, **23 students**, **5 full grading runs per model**, students graded **in
parallel** each run (OpenAI Tier 5). “Exact match” = same **total score** across all five runs for that student.
“Conf. flips” = fraction of graded entries where reported **confidence** changed
across runs (e.g. high → medium), per the experiment notes.

**Cost and wall time (sum across the 5 runs)**

| Model        | Cost (USD) | Total runtime |
|--------------|------------|---------------|
| gpt-5-mini   | $3.05      | 11m 5s        |
| gpt-5        | $18.12     | 26m 2s        |
| gpt-4.1-mini | $2.57      | 6m 45s        |
| gpt-4.1      | $12.07     | 6m 44s        |

**Stability across 5 runs**

| Model        | Exact match (students) | Max variance (pts) | Conf. flips |
|--------------|------------------------|--------------------|-------------|
| gpt-5-mini   | 13.0%                  | 6.5                | 9.7%        |
| gpt-5        | 13.0%                  | 5.0                | 7.9%        |
| gpt-4.1-mini | 47.8%                  | 3.0                | 1.0%        |
| gpt-4.1      | 65.2%                  | 1.0                | 0.0%        |

**Takeaway:** GPT-4.1-class models were **faster and far more score-stable** in this
setup; the dominant difference versus GPT-5 here is **temperature=0 vs 1**, not vision
or pricing alone.

---

## Vision Models: Pricing (approx.)

| Model | Input ($/1M) | Output ($/1M) | Best for |
|-------|--------------|---------------|----------|
| **gpt-5-nano** | $0.05 | $0.40 | Cheapest |
| **gpt-4.1-mini** | $0.40 | $1.60 | **Stable, cost-effective** |
| **gpt-5-mini** | $0.25 | $2.00 | Low cost (high variance) |
| **gpt-4.1** | $2.00 | $8.00 | **Most stable** |
| **gpt-5** | $1.25 | $10.00 | Flagship |
| **gpt-5.2** | $1.75 | $14.00 | Newer GPT-5 |

*Pricing from [OpenAI](https://openai.com/api/pricing/). Dated snapshots (e.g. gpt-5-mini-2025-08-07) use the same base model rates.*

---

## Recommended for Final Grading

**Use `gpt-4.1` or `gpt-4.1-mini`** when consistency matters — temperature=0 yields reproducible scores across regrades. Set it in your assignment config at `output/{assignment_name}/config.yaml`.

| Scenario | Model |
|----------|--------|
| **Stability-first** | gpt-4.1 (best) or gpt-4.1-mini |
| **Budget-first** | gpt-5-nano |
| **Best quality** | gpt-5 or gpt-5.2 |
| **Balance** | gpt-4.1-mini (stable + affordable) |

*gpt-5-mini is cheaper but has high score variance (temp=1); use only if cost outweighs consistency needs.*

---

## Image Token Pricing

Images are billed as input tokens (~500–1,000 per matplotlib plot). Charged at the model’s input rate.
