"""MIDI-based connection to FL Studio.

This module provides communication with FL Studio via MIDI messages and JSON files.
The approach is:
1. Write command data to a JSON file
2. Send a MIDI trigger note to FL Studio
3. FL Studio's MIDI controller script executes the command
4. Read the response from a JSON file

This is similar to how the piano_roll module works, but uses MIDI for triggering
instead of keystrokes.
"""

from __future__ import annotations

import json
import os
import platform
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any


def _get_fl_hardware_dir() -> Path:
    """Get the FL Studio Hardware scripts directory."""
    system = platform.system()

    if system in ("Darwin", "Windows"):
        base = Path.home() / "Documents" / "Image-Line" / "FL Studio" / "Settings"
    else:
        # Linux fallback
        base = Path.home() / ".fl-studio" / "Settings"

    hardware_dir = base / "Hardware" / "FLStudioMCP"
    hardware_dir.mkdir(parents=True, exist_ok=True)
    return hardware_dir


@contextmanager
def command_lock(path):
    """Serialize this mailbox across processes; OS releases locks on crash."""
    with open(path, "a+b") as handle:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            handle.write(b"0")
            handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class MIDIConnection:
    """MIDI-based connection to FL Studio.

    Communicates with FL Studio via:
    - JSON files for command/response data
    - MIDI trigger note to execute commands
    """

    # MIDI trigger note (same as in FL Studio controller script)
    TRIGGER_NOTE = 127

    def __init__(self) -> None:
        self._port = None
        self._port_name: str | None = None
        self._connected = False
        self._error: str | None = None
        self._thread_lock = threading.Lock()

        # File paths for JSON communication
        self._hardware_dir = _get_fl_hardware_dir()
        self._command_file = self._hardware_dir / "mcp_command.json"
        self._response_file = self._hardware_dir / "mcp_response.json"

    @property
    def is_connected(self) -> bool:
        """Check if connected to FL Studio."""
        return self._connected and self._port is not None

    @property
    def connection_error(self) -> str | None:
        """Get the last connection error, if any."""
        return self._error

    def connect(self) -> bool:
        """Attempt to connect to FL Studio via MIDI.

        Returns True if connection successful, False otherwise.
        """
        if self._connected and self._port is not None:
            return True

        try:
            import mido
        except ImportError:
            self._error = "mido library not installed. Install with: pip install mido python-rtmidi"
            return False

        # Find available MIDI output ports
        try:
            output_ports = mido.get_output_names()
        except Exception as e:
            self._error = f"Failed to get MIDI ports: {e}"
            return False

        if not output_ports:
            self._error = (
                "No MIDI output ports found. On Mac, enable IAC Driver in Audio MIDI Setup."
            )
            return False

        # Never send control notes into an arbitrary musical device.
        requested = os.environ.get("FL_MCP_MIDI_PORT")
        matches = (
            [p for p in output_ports if p == requested]
            if requested
            else [p for p in output_ports if "FLStudioMCP" in p]
        )
        if len(matches) != 1:
            self._error = "Set FL_MCP_MIDI_PORT to one dedicated output port; found: " + repr(
                matches
            )
            return False
        target_port = matches[0]

        try:
            self._port = mido.open_output(target_port)
            self._port_name = target_port
            self._connected = True
            self._error = None
            return True
        except Exception as e:
            self._error = f"Failed to open MIDI port '{target_port}': {e}"
            return False

    def disconnect(self) -> None:
        """Close the MIDI connection."""
        if self._port is not None:
            try:
                self._port.close()
            except Exception:
                pass
            self._port = None
        self._connected = False
        self._port_name = None

    def ensure_connected(self) -> None:
        """Ensure connection to FL Studio is active. Raises RuntimeError if not."""
        if not self.is_connected:
            if not self.connect():
                raise RuntimeError(self._error or "Failed to connect to FL Studio via MIDI")

    def send_command(
        self,
        action: str,
        params: dict[str, Any] | None = None,
        timeout: float = 2.0,
    ) -> dict[str, Any]:
        """Send a command to FL Studio and wait for response.

        Args:
            action: The command action (e.g., "transport.start", "mixer.setTrackVolume")
            params: Optional parameters for the command
            timeout: Maximum time to wait for response in seconds

        Returns:
            Response dictionary from FL Studio

        Raises:
            RuntimeError: If not connected or command fails
        """
        self.ensure_connected()
        with self._thread_lock, command_lock(self._hardware_dir / "mcp.lock"):
            request_id = uuid.uuid4().hex
            command = {"request_id": request_id, "action": action, "params": params or {}}
            temporary = self._command_file.with_suffix(".tmp")
            try:
                temporary.write_text(json.dumps(command), encoding="utf-8")
                os.replace(temporary, self._command_file)
                import mido

                self._port.send(mido.Message("note_on", note=self.TRIGGER_NOTE, velocity=127))
            except Exception as exc:
                return {"success": False, "error": str(exc), "request_id": request_id}
            return self._wait_for_response(timeout, request_id)

    def _wait_for_response(self, timeout: float, request_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                response = json.loads(self._response_file.read_text(encoding="utf-8"))
                if isinstance(response, dict) and response.get("request_id") == request_id:
                    return response
            except (OSError, ValueError):
                pass
            time.sleep(0.02)
        return {
            "success": False,
            "request_id": request_id,
            "error": "No matching FL response. Reload the updated controller; "
            "check FL Studio and the dedicated MIDI port. Outcome is unknown; "
            "inspect project before retrying a mutation.",
        }

    def get_status(self) -> dict[str, Any]:
        """Get connection status information."""
        try:
            import mido

            output_ports = mido.get_output_names()
        except Exception:
            output_ports = []

        reply = self.send_command("transport.getStatus") if self.is_connected else {}
        return {
            "connected": bool(reply.get("success")),
            "midi_port_open": self.is_connected,
            "port_name": self._port_name,
            "available_ports": output_ports,
            "command_file": str(self._command_file),
            "response_file": str(self._response_file),
            "error": self._error or reply.get("error"),
        }


# Global connection instance
_connection: MIDIConnection | None = None


def get_connection() -> MIDIConnection:
    """Get the global MIDI connection instance."""
    global _connection
    if _connection is None:
        _connection = MIDIConnection()
    return _connection


def reset_connection() -> None:
    """Reset the connection state to allow reconnection attempts."""
    global _connection
    if _connection is not None:
        _connection.disconnect()
        _connection = None
