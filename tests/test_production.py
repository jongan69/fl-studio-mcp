import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from fl_studio_mcp.tools.audio import measure, prepare_phrase, run, timing_plan
from fl_studio_mcp.utils.midi_connection import MIDIConnection


@pytest.fixture
def controller(monkeypatch):
    for name in ("channels", "midi", "mixer", "plugins", "transport", "patterns", "playlist"):
        monkeypatch.setitem(sys.modules, name, Mock())
    path = Path(__file__).parents[1] / "fl_controller/device_FLStudioMCP.py"
    spec = importlib.util.spec_from_file_location("controller", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_channel_api_argument_order(controller):
    controller.handle_channels_set_volume({"index": 2, "volume": 0.43})
    controller.channels.setChannelVolume.assert_called_once_with(2, 0.43, 0, True)
    controller.channels.getChannelVolume.assert_called_once_with(2, 0, True)
    controller.handle_channels_set_pan({"index": 3, "pan": -0.2})
    controller.channels.setChannelPan.assert_called_once_with(3, -0.2, 0, True)


def test_tempo_millibpm_and_bounds(controller):
    controller.mixer.getCurrentTempo.return_value = 126000
    assert controller.dispatch_command("transport.setTempo", {"bpm": 126})["bpm"] == 126
    controller.mixer.setCurrentTempo.assert_called_once_with(126000)
    with pytest.raises(ValueError):
        controller.dispatch_command("transport.setTempo", {"bpm": float("nan")})


def test_duplicate_trigger_executes_only_once(controller, tmp_path):
    controller.COMMAND_FILE = tmp_path / "command.json"
    controller.RESPONSE_FILE = tmp_path / "response.json"
    controller.COMMAND_FILE.write_text(json.dumps({"request_id": "one", "action": "test"}))
    controller.dispatch_command = Mock(return_value={"changed": True})
    controller.execute_pending_command()
    controller.execute_pending_command()
    controller.dispatch_command.assert_called_once()
    assert json.loads(controller.RESPONSE_FILE.read_text())["request_id"] == "one"


def test_error_is_not_success(controller, tmp_path):
    controller.COMMAND_FILE = tmp_path / "command.json"
    controller.RESPONSE_FILE = tmp_path / "response.json"
    controller.COMMAND_FILE.write_text(json.dumps({"request_id": "two", "action": "bad"}))
    controller.dispatch_command = Mock(return_value={"error": "unsupported"})
    controller.execute_pending_command()
    assert json.loads(controller.RESPONSE_FILE.read_text())["success"] is False


def test_stale_response_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "fl_studio_mcp.utils.midi_connection._get_fl_hardware_dir", lambda: tmp_path
    )
    conn = MIDIConnection()
    conn._response_file.write_text('{"success": true, "request_id": "old"}')
    assert conn._wait_for_response(0.03, "new")["success"] is False
    conn._response_file.write_text('{"success": true, "request_id": "new"}')
    assert conn._wait_for_response(0.03, "new")["success"] is True


def test_feedback_routing_refused(controller):
    controller.mixer.trackCount.return_value = 5
    controller.mixer.getRouteSendActive.side_effect = lambda a, b: (a, b) in {(2, 3), (3, 1)}
    with pytest.raises(ValueError, match="feedback"):
        controller.dispatch_command("mixer.setRoute", {"source": 1, "destination": 2})
    controller.mixer.setRouteTo.assert_not_called()


def test_half_double_and_real_alignment():
    assert timing_plan(92, 126)["tempo_ratio"] == pytest.approx(126 / 92)
    assert timing_plan(184, 126)["source_seconds"] == pytest.approx(
        timing_plan(92, 126)["source_seconds"] / 2
    )
    assert timing_plan(92, 126)["target_seconds"] == pytest.approx(32 * 60 / 126)
    for bpm in [0, -1, float("nan"), float("inf")]:
        with pytest.raises(ValueError):
            timing_plan(bpm, 126)


def test_real_ffmpeg_phrase_and_decode(tmp_path):
    source, output = tmp_path / "source.wav", tmp_path / "phrase.wav"
    run(
        "ffmpeg", ["-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=6", str(source)]
    )
    result = prepare_phrase(str(source), str(output), 92, 126, 0.1, bars=1)
    assert result["timing_pass"]
    assert result["output"]["audio"]["bits_per_sample"] == 24
    assert measure(str(output))["decode_pass"]
    with pytest.raises(ValueError, match="overwriting"):
        prepare_phrase(str(source), str(output), 92, 126, 0.1, bars=1)


def test_midi_roundtrip_and_overwrite_protection(tmp_path):
    import mido

    from fl_studio_mcp.tools.audio import export_midi

    notes = [{"midi": 60, "time": 0, "duration": 1}, {"midi": 60, "time": 1, "duration": 0.5}]
    path = tmp_path / "groove.mid"
    assert export_midi(notes, str(path))["notes"] == 2
    messages = [m.type for m in mido.MidiFile(path).tracks[0] if not m.is_meta]
    assert messages == ["note_on", "note_off", "note_on", "note_off"]
    with pytest.raises(ValueError):
        export_midi(notes, str(path))
    with pytest.raises(ValueError):
        export_midi([{"midi": 60, "time": 0, "duration": -1}], str(tmp_path / "bad.mid"))
    assert not (tmp_path / "bad.mid").exists()


def test_delivery_and_stem_contract(tmp_path):
    from fl_studio_mcp.tools.audio import check_stems, delivery_copies

    source = tmp_path / "master.wav"
    run(
        "ffmpeg",
        [
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=120:duration=1",
            "-ac",
            "2",
            str(source),
        ],
    )
    report = delivery_copies(
        str(source), str(tmp_path / "delivery.wav"), str(tmp_path / "listen.mp3")
    )
    assert all(x["decode_pass"] for x in report["exports"])
    assert report["exports"][0]["file"]["audio"]["bits_per_sample"] == 24
    assert report["exports"][1]["file"]["audio"]["bit_rate"] == "320000"
    assert check_stems([str(source), str(tmp_path / "delivery.wav")])["same_duration"]


def test_invalid_replace_queue_leaves_original_notes(monkeypatch, tmp_path):
    import importlib.machinery

    fake = Mock()
    fake.score.PPQ = 96
    monkeypatch.setitem(sys.modules, "flpianoroll", fake)
    path = Path(__file__).parents[1] / "scripts/ComposeWithLLM.pyscript"
    loader = importlib.machinery.SourceFileLoader("piano_test", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    module.REQUEST_FILE = str(tmp_path / "notes.json")
    Path(module.REQUEST_FILE).write_text(
        json.dumps(
            [{"action": "clear"}, {"action": "add_notes", "notes": [{"midi": 999, "duration": 1}]}]
        )
    )
    assert module.process_mcp_request()["status"] == "error"
    fake.score.deleteNote.assert_not_called()
    fake.score.addNote.assert_not_called()


def test_dedicated_port_required(monkeypatch, tmp_path):
    import mido

    monkeypatch.setattr(
        "fl_studio_mcp.utils.midi_connection._get_fl_hardware_dir", lambda: tmp_path
    )
    monkeypatch.delenv("FL_MCP_MIDI_PORT", raising=False)
    monkeypatch.setattr(mido, "get_output_names", lambda: ["USB keyboard", "IAC Driver Bus 1"])
    opener = Mock()
    monkeypatch.setattr(mido, "open_output", opener)
    assert not MIDIConnection().connect()
    opener.assert_not_called()
