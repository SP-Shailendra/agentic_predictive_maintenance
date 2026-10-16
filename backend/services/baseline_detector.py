import random
import uuid
from types import SimpleNamespace
from typing import Any

from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score
from sklearn.model_selection import train_test_split

from services.simulator_scenarios import build_scenario_reading

SCENARIOS = ("healthy_baseline", "vibration_rise", "overheating", "sudden_spike")
FEATURE_NAMES = ("vibration_ratio", "temperature_ratio", "pressure_bar", "rpm_ratio")


def _features(reading, vibration_threshold: float, temperature_threshold: float) -> list[float]:
    return [
        reading.vibration_mm_s / max(vibration_threshold, 0.1),
        reading.temperature_c / max(abs(temperature_threshold), 1.0),
        reading.pressure_bar,
        reading.rpm / 1450.0,
    ]


def build_scenario_dataset(samples_per_scenario: int = 250, seed: int = 42) -> tuple[list[list[float]], list[int], dict[str, int]]:
    if samples_per_scenario < 4:
        raise ValueError("At least four samples per scenario are required for a stratified evaluation split.")
    generator = random.Random(seed)
    features: list[list[float]] = []
    labels: list[int] = []
    counts: dict[str, int] = {}
    for scenario in SCENARIOS:
        counts[scenario] = 0
        for _ in range(samples_per_scenario):
            vibration_threshold = generator.uniform(5.0, 12.0)
            temperature_threshold = generator.uniform(70.0, 100.0)
            asset = SimpleNamespace(
                id=uuid.uuid4(),
                vibration_threshold_mm_s=vibration_threshold,
                temperature_threshold_c=temperature_threshold,
            )
            duration = generator.randint(2, 12)
            index = generator.randrange(duration)
            reading = build_scenario_reading(
                asset,
                scenario,
                generator.uniform(0.5, 1.0),
                index,
                duration,
            )
            if reading is None:
                continue
            features.append(_features(reading, vibration_threshold, temperature_threshold))
            labels.append(0 if scenario == "healthy_baseline" else 1)
            counts[scenario] += 1
    return features, labels, counts


def train_and_evaluate(samples_per_scenario: int = 250, seed: int = 42) -> tuple[Any, dict[str, object]]:
    features, labels, counts = build_scenario_dataset(samples_per_scenario, seed)
    train_features, test_features, train_labels, test_labels = train_test_split(
        features,
        labels,
        test_size=0.25,
        random_state=seed,
        stratify=labels,
    )
    model = RandomForestClassifier(
        n_estimators=200,
        min_samples_leaf=2,
        class_weight="balanced",
        random_state=seed,
    )
    model.fit(train_features, train_labels)
    predicted = model.predict(test_features)
    metrics = {
        "evaluation_source": "synthetic_simulator_scenarios",
        "positive_label": "anomalous_scenario",
        "scenario_counts": counts,
        "train_samples": len(train_labels),
        "test_samples": len(test_labels),
        "accuracy": float(accuracy_score(test_labels, predicted)),
        "precision": float(precision_score(test_labels, predicted, zero_division=0)),
        "recall": float(recall_score(test_labels, predicted, zero_division=0)),
        "f1": float(f1_score(test_labels, predicted, zero_division=0)),
        "features": list(FEATURE_NAMES),
        "seed": seed,
    }
    return model, metrics