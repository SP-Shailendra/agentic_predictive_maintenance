import argparse
import json
from pathlib import Path

import joblib

from services.baseline_detector import train_and_evaluate


def main() -> None:
    parser = argparse.ArgumentParser(description="Train and holdout-evaluate the synthetic healthy-baseline detector.")
    parser.add_argument("--samples-per-scenario", type=int, default=250)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path, help="Optional path to save the trained scikit-learn model.")
    args = parser.parse_args()
    if args.samples_per_scenario < 4:
        parser.error("--samples-per-scenario must be at least 4")

    model, metrics = train_and_evaluate(args.samples_per_scenario, args.seed)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(model, args.output)
        metrics["model_path"] = str(args.output)
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()