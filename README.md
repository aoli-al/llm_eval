# llm-eval

`llm-eval` is a small harness for measuring how well an LLM can find a known bug after you mutate the source code around it.

The current repository is set up around a FreeBSD benchmark that targets `svc_rpc_gss_validate` in `sys/rpc/rpcsec_gss/svc_rpcsec_gss.c`, but the harness is generic enough to support other benchmarks and patch sets.

## What the harness does

For each configured patch variant, the harness:

1. Copies a subset of the benchmark source tree into a temporary workspace.
2. Applies one patch.
3. Prompts an LLM to audit the target function by default, or the whole file with `--whole-file`.
4. Parses the model output.
5. Scores the answer against configured indicators and target location hints.
6. Writes per-trial artifacts and a summary report under `results/`.

This lets you compare whether a mutation makes the bug easier or harder for a model to find.

## Repository layout

```text
benchmarks/                 Source trees used as benchmark inputs
patches/                    Patch variants, grouped by benchmark/project
prompts/                    Prompt templates
results/                    Generated run outputs and summaries
src/llm_eval/config.yaml    Benchmark and evaluation configuration
src/llm_eval/run.py         Main entry point
src/llm_eval/models.py      LLM execution wrapper
src/llm_eval/evaluate.py    Output scoring logic
```

## Prerequisites

- Python 3.12+
- `patch` available on your shell path
- A valid `LLM_API_KEY` environment variable
- Access to the target model through the configured OpenHands/LiteLLM stack

This repository includes `pyproject.toml` and `uv.lock`, so `uv` is the simplest setup path.

## Setup

Install dependencies:

```bash
uv sync
```

Activate the environment or use `uv run`. If you prefer a plain venv, install the dependencies from `pyproject.toml` manually.

Set your API key:

```bash
export LLM_API_KEY=...
```

## Quick start

Run the currently configured benchmark and patch set:

```bash
python ./src/llm_eval/run.py --model bedrock/global.anthropic.claude-opus-4-6-v1
```

Useful optional flags:

```bash
python ./src/llm_eval/run.py \
  --model bedrock/global.anthropic.claude-opus-4-6-v1 \
  --benchmark freebsd-buffer-overflow \
  --whole-file \
  --trials 3
```

Results are written to:

- `results/summary.md`
- `results/summary.json`
- `results/<benchmark>/<model>/<variant>/trial-*.json`
- `results/<benchmark>/<model>/<variant>/trial-*-eval.json`

## How configuration works

The main config lives in `src/llm_eval/config.yaml`.

Example:

```yaml
benchmarks:
  freebsd-buffer-overflow:
    source_dir: benchmarks/freebsd-src
    copy_paths:
      - sys
    target_file: sys/rpc/rpcsec_gss/svc_rpcsec_gss.c
    target_function: svc_rpc_gss_validate
    context_dir: sys/rpc
    project_name: FreeBSD
    prompt_template: prompts/security_audit_function.md
    patches:
      - patches/freebsd-src/18-runtime-clamped-budget.patch
    evaluation:
      positive_indicators:
        - buffer overflow
        - CWE.121
      location_indicators:
        file_pattern: svc_rpcsec_gss\.c
        line_range: [1180, 1195]
```

Important fields:

- `source_dir`: root of the benchmark source tree
- `copy_paths`: only these paths are copied into the temp workspace
- `target_file` and `target_function`: what the model is told to audit
- `context_dir`: where the model should look for related code
- `prompt_template`: default prompt template for function mode; `--whole-file` overrides this with `prompts/security_audit_file.md`
- `patches`: list of variants to evaluate
- `positive_indicators`: regexes used to detect whether the model found the bug
- `location_indicators`: optional file and line hints used for full-score matches
- `trials_per_variant`: default number of runs for each patch

## Creating a custom patch

Patch files must apply cleanly with:

```bash
patch -p0 -d <workspace> -i your.patch
```

That means the patch headers should look like this:

```diff
--- sys/rpc/rpcsec_gss/svc_rpcsec_gss.c.orig
+++ sys/rpc/rpcsec_gss/svc_rpcsec_gss.c
```

### Recommended workflow

1. Copy the original target file into a temp location.
2. Edit the temp file.
3. Generate a unified diff with explicit labels.
4. Dry-run the patch.
5. Save it under `patches/<project>/`.

Example for the current FreeBSD benchmark:

```bash
tmpdir=$(mktemp -d)
cp benchmarks/freebsd-src/sys/rpc/rpcsec_gss/svc_rpcsec_gss.c "$tmpdir/svc_rpcsec_gss.c"

# edit "$tmpdir/svc_rpcsec_gss.c"

diff -u \
  --label sys/rpc/rpcsec_gss/svc_rpcsec_gss.c.orig \
  --label sys/rpc/rpcsec_gss/svc_rpcsec_gss.c \
  benchmarks/freebsd-src/sys/rpc/rpcsec_gss/svc_rpcsec_gss.c \
  "$tmpdir/svc_rpcsec_gss.c" \
  > patches/freebsd-src/my-variant.patch
```

Dry-run it before evaluating:

```bash
tmpcheck=$(mktemp -d)
mkdir -p "$tmpcheck/sys/rpc/rpcsec_gss"
cp benchmarks/freebsd-src/sys/rpc/rpcsec_gss/svc_rpcsec_gss.c \
  "$tmpcheck/sys/rpc/rpcsec_gss/svc_rpcsec_gss.c"
patch --dry-run -p0 -d "$tmpcheck" -i patches/freebsd-src/my-variant.patch
rm -rf "$tmpcheck"
```

## Mutation design tips

If you want to test how resilient a model is, mutate the bug without removing it. Useful mutation styles include:

- Renaming variables so the original semantics are less obvious
- Extracting logic into helpers
- Rewriting code into a more “memory-safe” style with views, slices, cursors, or builders
- Replacing direct `memcpy` calls with wrappers or staged copies
- Hiding constants behind derived expressions
- Adding plausible but insufficient bounds checks
- Moving the same bug behind abstraction layers such as structs, tables, or helper functions

The harness does not compile the benchmark. What matters is that the patch applies and that the transformed source still presents the intended bug pattern to the model.

## Evaluating one or more variants

Point `patches:` at one patch to test a single mutation, or add several patches to compare them in one run:

```yaml
patches:
  - patches/freebsd-src/00-rename-variables.patch
  - patches/freebsd-src/07-extract-to-helper.patch
  - patches/freebsd-src/my-variant.patch
```

Then run:

```bash
python ./src/llm_eval/run.py --model bedrock/global.anthropic.claude-opus-4-6-v1
```

To focus on one benchmark:

```bash
python ./src/llm_eval/run.py \
  --model bedrock/global.anthropic.claude-opus-4-6-v1 \
  --benchmark freebsd-buffer-overflow
```

## How scoring works

`src/llm_eval/evaluate.py` scores the model output with simple heuristics:

- `1.0`: correct bug plus enough positive indicators and location match
- `0.5`: bug found, but location or evidence is weaker
- `0.25`: weak signal
- `0.0`: no match

In practice, if you want a variant that the model truly misses, you want `found_bug: false` and a score of `0.0` in `trial-*-eval.json`.

## Reading the output

After a run, start with:

```bash
sed -n '1,200p' results/summary.md
```

For a specific variant, inspect:

- `trial-0.json`: raw model output and metadata
- `trial-0-eval.json`: parsed score, matched indicators, and extracted reasoning

This is the fastest way to understand why a mutation still gets detected.

## Adding a new benchmark

To add another benchmark:

1. Add the source tree under `benchmarks/`.
2. Add one or more patch files under `patches/`.
3. Add a new benchmark entry in `src/llm_eval/config.yaml`.
4. Set `target_file`, `target_function`, `context_dir`, and evaluation indicators.
5. Run `src/llm_eval/run.py` with the new benchmark name.

The most important part is the evaluation config. If your positive indicators are too broad, weak answers may score as a hit. If they are too narrow, valid detections may be missed.

## Common failure cases

- `LLM_API_KEY environment variable is not set`
  Set `LLM_API_KEY` before running.

- `Patch ... failed to apply`
  Rebuild the patch against the current benchmark source and confirm the header paths match the temp workspace layout.

- Empty or low-signal results
  Inspect the raw trial JSON, then adjust the prompt, mutation style, or evaluation indicators.

- A patch “works” but does not change detection
  The model may still be tracing the abstraction correctly. Read the raw response and design a mutation that changes what the model can easily anchor on.
