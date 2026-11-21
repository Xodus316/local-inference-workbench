"""Keep this local hardware adapter attached only to the public runtime."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_ROOT = ROOT


def assert_public_runtime():
    import orchestrator
    expected = (PUBLIC_ROOT / "orchestrator").resolve()
    if Path(orchestrator.__file__).resolve().parent != expected:
        raise RuntimeError("Fresh native execution requires the selected public orchestrator package")
    for name, module in tuple(sys.modules.items()):
        if name == "orchestrator" or name.startswith("orchestrator."):
            location = getattr(module, "__file__", None)
            if location is not None and not Path(location).resolve().is_relative_to(expected):
                raise RuntimeError("A legacy orchestrator submodule contaminated the fresh runtime")
    return str(expected)


assert_public_runtime()
