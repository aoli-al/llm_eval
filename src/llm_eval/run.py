#!/usr/bin/env python3
import argparse
import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

from evaluate import EvalResult, evaluate_response
from models import RunResult, run_model

ROOT = Path(__file__).resolve().parent.parent.parent


def load_config(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def render_prompt(template_path: Path, params: dict) -> str:
    text = template_path.read_text()
    for key, value in params.items():
        text = text.replace(f"{{{{{key}}}}}", str(value))
    return text


def prepare_source(benchmark: dict, patch_file: Path | None = None) -> Path:
    import subprocess

    tmp = Path(tempfile.mkdtemp(prefix="llm-eval-"))
    source_dir = ROOT / benchmark["source_dir"]
    for cp in benchmark["copy_paths"]:
        src = source_dir / cp
        dst = tmp / cp
        if src.is_dir():
            shutil.copytree(src, dst, symlinks=True)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    if patch_file:
        print(f"  Applying patch: {patch_file.name}")
        result = subprocess.run(
            ["patch", "-p0", "-d", str(tmp), "-i", str(patch_file)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"  WARNING: Patch failed: {result.stderr}")
            shutil.rmtree(tmp)
            raise RuntimeError(f"Patch {patch_file} failed to apply")

    return tmp


def save_result(
    output_dir: Path,
    trial_idx: int,
    run_result: RunResult,
    eval_result: EvalResult,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    raw_path = output_dir / f"trial-{trial_idx}.json"
    raw_path.write_text(json.dumps({
        "stdout": run_result.stdout,
        "stderr": run_result.stderr,
        "exit_code": run_result.exit_code,
        "duration_seconds": run_result.duration_seconds,
        "model_name": run_result.model_name,
    }, indent=2))

    eval_path = output_dir / f"trial-{trial_idx}-eval.json"
    eval_path.write_text(json.dumps({
        "score": eval_result.score,
        "found_bug": eval_result.found_bug,
        "matched_indicators": eval_result.matched_indicators,
        "matched_location": eval_result.matched_location,
        "reasoning_excerpt": eval_result.reasoning_excerpt,
    }, indent=2))


def run_benchmark(
    config: dict,
    benchmark_name: str,
    model_name: str,
    num_trials: int,
) -> dict:
    benchmark = config["benchmarks"][benchmark_name]
    eval_config = benchmark["evaluation"]
    model_defaults = config.get("model_defaults", {})

    template_path = ROOT / benchmark["prompt_template"]
    prompt = render_prompt(template_path, {
        "project_name": benchmark["project_name"],
        "target_file": benchmark["target_file"],
        "target_function": benchmark["target_function"],
        "context_dir": benchmark["context_dir"],
    })

    results_base = ROOT / "results" / benchmark_name / model_name

    variants = []
    for patch_path_str in benchmark.get("patches", []):
        patch_path = ROOT / patch_path_str
        if patch_path.exists():
            variant_name = patch_path.stem
            variants.append((variant_name, patch_path))
        else:
            print(f"WARNING: Patch not found: {patch_path}")

    summary = {"benchmark": benchmark_name, "model": model_name, "variants": {}}

    for variant_name, patch_file in variants:
        print(f"\n=== Variant: {variant_name} ===")
        variant_dir = results_base / variant_name
        scores = []

        for trial in range(num_trials):
            print(f"  Trial {trial + 1}/{num_trials}...")
            source_dir = None
            try:
                source_dir = prepare_source(benchmark, patch_file)
                run_result = run_model(model_name, prompt, source_dir, model_defaults)
                eval_result = evaluate_response(run_result.stdout, eval_config)
                save_result(variant_dir, trial, run_result, eval_result)
                scores.append(eval_result.score)
                print(f"    Score: {eval_result.score}, Found: {eval_result.found_bug}")
                if eval_result.matched_indicators:
                    print(f"    Matched: {eval_result.matched_indicators}")
            except Exception as e:
                print(f"    ERROR: {e}")
                scores.append(0.0)
            finally:
                if source_dir and source_dir.exists():
                    shutil.rmtree(source_dir, ignore_errors=True)

        avg_score = sum(scores) / len(scores) if scores else 0.0
        detection_rate = sum(1 for s in scores if s > 0) / len(scores) if scores else 0.0

        summary["variants"][variant_name] = {
            "trials": num_trials,
            "scores": scores,
            "avg_score": round(avg_score, 3),
            "detection_rate": round(detection_rate, 3),
        }

    return summary


def generate_report(summaries: list[dict], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    all_summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "benchmarks": summaries,
    }
    (output_dir / "summary.json").write_text(json.dumps(all_summary, indent=2))

    lines = ["# LLM Bug-Finding Evaluation Results\n"]
    lines.append(f"Generated: {all_summary['timestamp']}\n")

    for s in summaries:
        lines.append(f"\n## {s['benchmark']} (model: {s['model']})\n")
        lines.append("| Variant | Detection Rate | Avg Score | Scores |")
        lines.append("|---------|---------------|-----------|--------|")
        for name, data in s["variants"].items():
            rate = f"{data['detection_rate'] * 100:.0f}%"
            avg = f"{data['avg_score']:.2f}"
            scores_str = ", ".join(f"{sc:.1f}" for sc in data["scores"])
            lines.append(f"| {name} | {rate} | {avg} | {scores_str} |")

    (output_dir / "summary.md").write_text("\n".join(lines) + "\n")
    print(f"\nReport written to {output_dir / 'summary.md'}")


def main():
    parser = argparse.ArgumentParser(description="LLM bug-finding evaluation harness")
    parser.add_argument(
        "--config", default=str(ROOT / "src" / "llm_eval" / "config.yaml"),
        help="Path to config YAML",
    )
    parser.add_argument("--benchmark", help="Run specific benchmark (default: all)")
    parser.add_argument(
        "--model", required=True,
        help="Any litellm model string (e.g. 'anthropic/claude-sonnet-4-6-20250514', "
             "'openai/gpt-4o', 'ollama/llama3')",
    )
    parser.add_argument("--trials", type=int, help="Override trials per variant")
    args = parser.parse_args()

    config = load_config(Path(args.config))
    num_trials = args.trials or config.get("trials_per_variant", 3)

    benchmarks = (
        [args.benchmark] if args.benchmark
        else list(config["benchmarks"].keys())
    )

    summaries = []
    for bm in benchmarks:
        print(f"\n{'=' * 60}")
        print(f"Benchmark: {bm}")
        print(f"{'=' * 60}")
        summary = run_benchmark(config, bm, args.model, num_trials)
        summaries.append(summary)

    generate_report(summaries, ROOT / "results")


if __name__ == "__main__":
    main()
