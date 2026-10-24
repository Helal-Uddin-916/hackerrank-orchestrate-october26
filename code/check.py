"""One-command quality gate: run every check and say pass or fail.

Usage (from the repo root):
    python3 code/check.py

Runs, in order:
1. all unit tests in code/tests;
2. builds the hard cases (code/evaluation/custom_cases.py) and checks their expected answers;
3. checks output.csv at the repo root against the format rules, if it exists;
4. runs the agent (code/main.py) on the 32 samples and grades it: status accuracy and step precision
   must stay at 100% and step recall at 90% or more (today's level);
5. runs the agent on the hard cases (no AI) and grades it: every graded case must be exactly right.
Exits with status 1 if any check fails. Skipped checks are listed, never hidden.
"""
import importlib.util
import os
import subprocess
import sys
import tempfile

SAMPLE_FLOOR = {"status_accuracy": 1.0, "step_precision": 1.0, "step_recall": 0.90}

CODE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(CODE_DIR)


def run(label, args):
    """Run one check as a separate process; return (label, 'PASS'/'FAIL', last output line)."""
    result = subprocess.run([sys.executable] + args, cwd=REPO_ROOT, capture_output=True, text=True)
    output = (result.stdout + result.stderr).strip().splitlines()
    status = "PASS" if result.returncode == 0 else "FAIL"
    if status == "FAIL":
        print(f"--- {label} output ---")
        print("\n".join(output[-40:]))
    return label, status, output[-1] if output else ""


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def grade_agent(label, main_args, expected_csv, passes):
    """Run code/main.py with main_args, grade against expected_csv, apply passes(scores) -> (ok, detail)."""
    with tempfile.TemporaryDirectory() as tmp:
        output = os.path.join(tmp, "output.csv")
        args = [os.path.join(CODE_DIR, "main.py"), "--output", output, "--reports-dir", tmp] + main_args
        result = subprocess.run([sys.executable] + args, cwd=REPO_ROOT, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"--- {label} output ---\n{result.stdout}{result.stderr}")
            return label, "FAIL", "agent run failed"
        grader = _load("evaluation_main", os.path.join(CODE_DIR, "evaluation", "main.py"))
        scores = grader.evaluate(output, expected_csv())
    ok, detail = passes(scores)
    return label, "PASS" if ok else "FAIL", detail


def main():
    results = [run("unit tests", ["-m", "unittest", "discover", "-s", os.path.join(CODE_DIR, "tests")])]

    with tempfile.TemporaryDirectory() as cases_dir:
        results.append(run("hard cases build", [os.path.join(CODE_DIR, "evaluation", "custom_cases.py"), cases_dir]))

    output_csv = os.path.join(REPO_ROOT, "output.csv")
    if os.path.exists(output_csv):
        results.append(run("output.csv format", [os.path.join(CODE_DIR, "validate_output.py"), output_csv]))
    else:
        results.append(("output.csv format", "SKIP", "no output.csv yet"))

    dataset = os.path.join(REPO_ROOT, "dataset")

    def samples_pass(scores):
        ok = all(scores[k] >= floor for k, floor in SAMPLE_FLOOR.items())
        return ok, (f"status {scores['status_accuracy']:.0%}, precision {scores['step_precision']:.0%}, "
                    f"recall {scores['step_recall']:.1%}, exact {scores['exact_case_match']:.0%}")

    results.append(grade_agent("agent on samples",
                               ["--requests", os.path.join(dataset, "sample_deletion_requests.csv")],
                               lambda: os.path.join(dataset, "sample_output.csv"), samples_pass))

    def hard_pass(scores):
        wrong = sorted(c for c, r in scores["per_case"].items() if not r["exact"])
        return not wrong, f"{scores['cases'] - len(wrong)}/{scores['cases']} exact" + (f"; wrong: {wrong}" if wrong else "")

    with tempfile.TemporaryDirectory() as cases_dir:
        _load("custom_cases", os.path.join(CODE_DIR, "evaluation", "custom_cases.py")).build(cases_dir)
        results.append(grade_agent("agent on hard cases",
                                   ["--dataset", cases_dir, "--requests", os.path.join(cases_dir, "graded_requests.csv"), "--no-ai"],
                                   lambda: os.path.join(cases_dir, "expected_output.csv"), hard_pass))

    print()
    for label, status, detail in results:
        print(f"{status:4}  {label:20} {detail}")
    failed = [label for label, status, _ in results if status == "FAIL"]
    print("\nRESULT:", "FAIL (" + ", ".join(failed) + ")" if failed else "PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
