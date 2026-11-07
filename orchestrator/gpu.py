"""Read display-only telemetry for the first NVIDIA GPU."""
from __future__ import annotations
import csv
import math
import subprocess
from typing import Any

GPU_QUERY = (
    "name,memory.used,memory.total,memory.free,utilization.gpu,"
    "temperature.gpu,power.draw,power.limit"
)

def read_gpu_stats() -> dict[str, Any]:
    """Read the first NVIDIA GPU without blocking the panel indefinitely."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    completed = subprocess.run(
        [
            "nvidia-smi", f"--query-gpu={GPU_QUERY}",
            "--format=csv,noheader,nounits",
        ],
        capture_output=True,
        text=True,
        creationflags=flags,
        timeout=5,
        check=False,
    )
    if completed.returncode != 0:
        raise OSError("NVIDIA telemetry command failed")
    try:
        rows = list(csv.reader(line for line in completed.stdout.splitlines() if line.strip()))
    except (csv.Error, AttributeError):
        raise ValueError("Invalid NVIDIA telemetry response") from None
    if not rows or len(rows[0]) != 8:
        raise ValueError("Invalid NVIDIA telemetry response")
    row = [value.strip() for value in rows[0]]
    try:
        values = {
            "name": row[0],
            "memory_used_mib": int(float(row[1])),
            "memory_total_mib": int(float(row[2])),
            "memory_free_mib": int(float(row[3])),
            "utilization_percent": float(row[4]),
            "temperature_c": float(row[5]),
            "power_draw_w": float(row[6]),
            "power_limit_w": float(row[7]),
        }
    except (TypeError, ValueError, OverflowError):
        raise ValueError("Invalid NVIDIA telemetry values") from None
    numbers = [value for key, value in values.items() if key != "name"]
    if (
        not values["name"] or len(values["name"]) > 100
        or any(not math.isfinite(value) for value in numbers)
        or values["memory_total_mib"] <= 0
        or not 0 <= values["memory_used_mib"] <= values["memory_total_mib"]
        or not 0 <= values["memory_free_mib"] <= values["memory_total_mib"]
        or not 0 <= values["utilization_percent"] <= 100
        or not -100 <= values["temperature_c"] <= 300
        or values["power_draw_w"] < 0
        or values["power_limit_w"] <= 0
    ):
        raise ValueError("NVIDIA telemetry values exceed display bounds")
    return values


def _gpu_display_values(stats: dict[str, Any]) -> dict[str, str]:
    used = f'{stats["memory_used_mib"]:,}'
    total = f'{stats["memory_total_mib"]:,}'
    free = f'{stats["memory_free_mib"]:,}'
    return {
        "name": str(stats["name"]),
        "vram": f"{used} MiB used / {total} MiB total",
        "free": f"{free} MiB",
        "utilization": f'{stats["utilization_percent"]:.0f}%',
        "temperature": f'{stats["temperature_c"]:.0f}°C',
        "power": f'{stats["power_draw_w"]:.1f} W / {stats["power_limit_w"]:.0f} W',
    }
