"""Install this checkout's FL scripts with timestamped backups. No UI automation."""

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


def install():
    root = Path(__file__).resolve().parents[1]
    settings = Path.home() / "Documents" / "Image-Line" / "FL Studio" / "Settings"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    receipt = []
    for relative, destination in [
        (
            "fl_controller/device_FLStudioMCP.py",
            settings / "Hardware/FLStudioMCP/device_FLStudioMCP.py",
        ),
        (
            "scripts/ComposeWithLLM.pyscript",
            settings / "Piano roll scripts/ComposeWithLLM.pyscript",
        ),
    ]:
        source = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        old = None
        if destination.exists():
            old = settings / "MCP Backups" / stamp / destination.name
            old.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destination, old)
        shutil.copy2(source, destination)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        assert hashlib.sha256(destination.read_bytes()).hexdigest() == digest
        receipt.append(
            {"file": str(destination), "backup": str(old) if old else None, "sha256": digest}
        )
    print(
        json.dumps(
            {
                "installed": receipt,
                "next": "Reload MIDI controller in FL Script output; "
                "reopen the piano roll script before using notes.",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    install()
