from typing import Literal

FailureMode = Literal[
    "bearing_wear",
    "imbalance",
    "misalignment",
    "lubrication",
    "cooling_restriction",
    "overheating",
    "friction",
    "overload",
    "sensor_fault",
    "other",
]

FAILURE_MODE_VALUES = (
    "bearing_wear",
    "imbalance",
    "misalignment",
    "lubrication",
    "cooling_restriction",
    "overheating",
    "friction",
    "overload",
    "sensor_fault",
    "other",
)