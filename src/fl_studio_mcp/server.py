"""FL Studio MCP Server - Control FL Studio via Model Context Protocol.

This MCP server provides tools to control FL Studio through two mechanisms:
1. MIDI + JSON - Real-time control of mixer, transport, channels, and plugins
2. Piano Roll Scripts - Persistent note placement via JSON + keystroke triggering

Requirements:
- FL Studio 20.7+ running
- FLStudioMCP controller script installed (run: ./scripts/setup_mac.sh)
- On Mac: IAC Driver enabled in Audio MIDI Setup
- On Windows: loopMIDI virtual MIDI ports configured
- For piano roll: ComposeWithLLM.pyscript installed in FL Studio

Limitations:
- Cannot load new plugins (only control existing ones)
- Playlist clip placement and native rendering need GUI assistance
"""

from __future__ import annotations

from fastmcp import FastMCP

from fl_studio_mcp.tools import (
    register_channel_tools,
    register_mixer_tools,
    register_piano_roll_tools,
    register_plugin_tools,
    register_transport_tools,
)
from fl_studio_mcp.tools.audio import register_audio_tools
from fl_studio_mcp.utils.connection import get_connection, reset_connection

# Create the MCP server
mcp = FastMCP(
    name="fl-studio-mcp",
    instructions="""
FL Studio MCP Server - Control FL Studio from AI assistants.

This server provides tools to control FL Studio through its Python scripting API.
FL Studio must be running with the FLStudioMCP MIDI controller enabled.

Available tool categories:
- Transport: Play, stop, record, tempo, position control
- Mixer: Volume, pan, mute, solo, track management
- Channels: Channel info, note triggering, step sequencer
- Plugins: Parameter control, preset navigation (cannot load new plugins)

Important limitations:
1. Cannot load new VST/AU plugins - only control existing ones
2. Playlist placement, plugin loading and native rendering require GUI assistance
3. Note triggering (fl_trigger_note) is real-time only - notes won't persist
   unless FL Studio is recording. Use step sequencer (fl_set_grid_bit) for
   persistent drum patterns.
""",
)


# Register connection status resource
@mcp.resource("fl://status")
def get_fl_status() -> str:
    """Get FL Studio connection status."""
    conn = get_connection()
    if conn.get_status()["connected"]:
        return "FL Studio responded via MIDI"
    else:
        return f"Not connected: {conn.connection_error}"


@mcp.resource("fl://project")
def get_project_info() -> dict:
    """Get current FL Studio project information."""
    conn = get_connection()

    if not conn.is_connected:
        return {"error": conn.connection_error}

    try:
        result = conn.send_command("transport.getStatus")
        if not result.get("success", False) and "error" in result:
            return {"error": result["error"]}

        return {
            "is_playing": result.get("is_playing", False),
            "is_recording": result.get("is_recording", False),
            "position": result.get("position", ""),
            "loop_mode": result.get("loop_mode", "pattern"),
        }
    except Exception as e:
        return {"error": str(e)}


# Connection management tools
@mcp.tool()
def fl_connect() -> str:
    """Connect or reconnect to FL Studio via MIDI.

    Use this tool to:
    - Check if FL Studio is connected
    - Retry connection after starting FL Studio
    - Reconnect if the connection was lost

    Returns the connection status.
    """
    # Reset connection state to force a fresh connection attempt
    reset_connection()

    conn = get_connection()
    try:
        conn.ensure_connected()
    except RuntimeError as e:
        return f"Connection failed: {e}"
    status = conn.get_status()
    return "FL Studio responded successfully" if status["connected"] else str(status["error"])


@mcp.tool()
def fl_connection_status() -> dict:
    """Get the current FL Studio connection status.

    Returns information about whether FL Studio is connected
    and any error messages if not.
    """
    conn = get_connection()
    # Eagerly attempt a connection so the reported status reflects actual
    # reachability rather than the lazily-initialized flag.
    try:
        conn.ensure_connected()
    except RuntimeError:
        pass
    status = conn.get_status()
    return {
        "connected": status.get("connected", False),
        "port_name": status.get("port_name"),
        "available_ports": status.get("available_ports", []),
        "error": status.get("error"),
    }


@mcp.tool()
def fl_get_tempo() -> dict:
    """Read the live FL Studio project tempo."""
    return get_connection().send_command("transport.getTempo")


@mcp.tool()
def fl_set_tempo(bpm: float) -> dict:
    """Set project tempo between 40 and 240 BPM and read it back."""
    return get_connection().send_command("transport.setTempo", {"bpm": bpm})


@mcp.tool()
def fl_prepare_pattern(index: int, name: str) -> dict:
    """Select/create and name a pattern. Does not clear its existing notes."""
    return get_connection().send_command("patterns.prepare", {"index": index, "name": name})


@mcp.tool()
def fl_select_pattern(index: int) -> dict:
    """Select an existing pattern by one-based index."""
    return get_connection().send_command("patterns.select", {"index": index})


@mcp.tool()
def fl_setup_rebuild_routing() -> dict:
    """Route the dedicated rebuild template into drum, music and vocal buses.

    Use only in the purpose-built template, never an arbitrary user project.
    """
    return get_connection().send_command("mixer.setupRebuildRouting")


@mcp.tool()
def fl_show_channel_editor(index: int) -> dict:
    """Open an existing channel's editor without loading or changing its plugin."""
    return get_connection().send_command("channels.showEditor", {"index": index})


@mcp.tool()
def fl_list_patterns() -> dict:
    """Read names and selection of native patterns from FL Studio."""
    return get_connection().send_command("patterns.list")


@mcp.tool()
def fl_set_mixer_route(source: int, destination: int, enabled: bool = True) -> dict:
    """Set one route with feedback prevention and live readback. Other routes remain intact."""
    return get_connection().send_command(
        "mixer.setRoute",
        {"source": source, "destination": destination, "enabled": enabled},
        timeout=10,
    )


register_audio_tools(mcp)

# Register all tools
register_transport_tools(mcp)
register_mixer_tools(mcp)
register_channel_tools(mcp)
register_plugin_tools(mcp)
register_piano_roll_tools(mcp)


def main():
    """Run the FL Studio MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
