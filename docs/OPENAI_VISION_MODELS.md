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
| gpt-5-mini | Yes | **Recommended for grading** |
| gpt-5-nano-2025-08-07 | Yes | Dated snapshot |
| gpt-5.2 | Yes | GPT-5.2 series |
| gpt-3.5-turbo-16k | **No** | Text only, no vision |

---

## Temperature (deterministic grading)

- **GPT-5 family** (gpt-5, gpt-5-mini, gpt-5-nano): Only support temperature=1. Regrades can vary.
- **GPT-4.1, GPT-4.1-mini, GPT-4o-mini**: Support temperature=0 for deterministic, reproducible grading.

Use **gpt-4.1-mini** or **gpt-4o-mini** if you need consistent scores on regrade.

---

## Vision Models: Pricing (approx.)

| Model | Input ($/1M) | Output ($/1M) | Best for |
|-------|--------------|---------------|----------|
| **gpt-5-nano** | $0.05 | $0.40 | Cheapest |
| **gpt-4.1-mini** | $0.40 | $1.60 | Good balance |
| **gpt-5-mini** | $0.25 | $2.00 | **Final grading** |
| **gpt-4.1** | $2.00 | $8.00 | Higher quality |
| **gpt-5** | $1.25 | $10.00 | Flagship |
| **gpt-5.2** | $1.75 | $14.00 | Newer GPT-5 |

*Pricing from [OpenAI](https://openai.com/api/pricing/). Dated snapshots (e.g. gpt-5-mini-2025-08-07) use the same base model rates.*

---

## Recommended for Final Grading

**Use `gpt-5-mini`** — low cost, full vision, good for structured grading. Already set in your `config.yaml`.

| Scenario | Model |
|----------|--------|
| **Budget-first** | gpt-5-nano |
| **Best quality** | gpt-5 or gpt-5.2 |
| **Balance** | gpt-5-mini or gpt-4.1-mini |

---

## Image Token Pricing

Images are billed as input tokens (~500–1,000 per matplotlib plot). Charged at the model’s input rate.
