"""Reproducible offline audio work. Requires ffmpeg/ffprobe on PATH.

Always create new files; never overwrite a source. Loudness checks are technical
measurements, not listening approval. Tempo inputs must be musically verified.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
from pathlib import Path


def run(program: str, args: list[str]) -> subprocess.CompletedProcess:
    executable = shutil.which(program)
    if not executable:
        raise RuntimeError(f"Install {program} and make it available on PATH")
    return subprocess.run(
        [executable, *args], capture_output=True, text=True, check=True, timeout=900
    )


def probe(path: str) -> dict:
    source = Path(path).expanduser().resolve(strict=True)
    data = json.loads(
        run(
            "ffprobe", ["-v", "error", "-show_format", "-show_streams", "-of", "json", str(source)]
        ).stdout
    )
    audio = [s for s in data["streams"] if s["codec_type"] == "audio"]
    if len(audio) != 1:
        raise ValueError("Expected exactly one audio stream")
    with source.open("rb") as handle:
        hasher = hashlib.sha256()
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(block)
        digest = hasher.hexdigest()
    return {"path": str(source), "sha256": digest, "format": data["format"], "audio": audio[0]}


def timing_plan(
    source_bpm: float, target_bpm: float, bars: int = 8, beats_per_bar: int = 4
) -> dict:
    if not all(math.isfinite(x) and x > 0 for x in (source_bpm, target_bpm)):
        raise ValueError("BPM must be finite and positive")
    if not 1 <= bars <= 512 or not 1 <= beats_per_bar <= 16:
        raise ValueError("Invalid musical phrase length")
    beats = bars * beats_per_bar
    return {
        "source_bpm": source_bpm,
        "target_bpm": target_bpm,
        "tempo_ratio": target_bpm / source_bpm,
        "bars": bars,
        "beats_per_bar": beats_per_bar,
        "beats": beats,
        "source_seconds": beats * 60 / source_bpm,
        "target_seconds": beats * 60 / target_bpm,
        "assumption": "Constant source tempo; BPM/downbeat must be verified by listening. "
        "Half/double time are possible; a 4/3 reinterpretation is not beat alignment.",
    }


def prepare_phrase(
    source: str,
    output: str,
    source_bpm: float,
    target_bpm: float,
    start_seconds: float,
    bars: int = 8,
    sample_rate: int = 48000,
) -> dict:
    plan = timing_plan(source_bpm, target_bpm, bars)
    if not math.isfinite(start_seconds) or start_seconds < 0:
        raise ValueError("Invalid phrase start")
    if sample_rate not in (44100, 48000, 88200, 96000):
        raise ValueError("Unsupported project sample rate")
    info = probe(source)
    if start_seconds + plan["source_seconds"] > float(info["format"]["duration"]) + 0.001:
        raise ValueError("Phrase extends beyond recording")
    destination = Path(output).expanduser().resolve()
    if destination.suffix.lower() != ".wav" or destination.exists():
        raise ValueError("Use a new .wav destination; overwriting is prohibited")
    ratio = plan["tempo_ratio"]
    factors = []
    while ratio > 2:
        factors.append(2.0)
        ratio /= 2
    while ratio < 0.5:
        factors.append(0.5)
        ratio /= 0.5
    factors.append(ratio)
    filters = [
        f"atrim=start={start_seconds}:duration={plan['source_seconds']}",
        "asetpts=PTS-STARTPTS",
        *[f"atempo={x:.12g}" for x in factors],
    ]
    run(
        "ffmpeg",
        [
            "-v",
            "error",
            "-n",
            "-i",
            info["path"],
            "-map",
            "0:a:0",
            "-af",
            ",".join(filters),
            "-ar",
            str(sample_rate),
            "-c:a",
            "pcm_s24le",
            str(destination),
        ],
    )
    result = probe(str(destination))
    error = float(result["format"]["duration"]) - plan["target_seconds"]
    return {
        "source": info,
        "output": result,
        "plan": plan,
        "duration_error_seconds": error,
        "timing_pass": abs(error) <= 0.05,
        "quality": "FFmpeg atempo pitch-preserving preview; audition for artifacts. "
        "Use FL Stretch Pro for final vocals when it sounds better.",
    }


def measure(path: str) -> dict:
    info = probe(path)
    report = run(
        "ffmpeg",
        [
            "-hide_banner",
            "-nostats",
            "-i",
            info["path"],
            "-map",
            "0:a:0",
            "-af",
            "loudnorm=I=-9:TP=-1:LRA=9:print_format=json",
            "-f",
            "null",
            "-",
        ],
    ).stderr
    start, end = report.rfind("{"), report.rfind("}")
    loudness = json.loads(report[start : end + 1])
    # Full decode must complete without errors; loudnorm output is discarded.
    run(
        "ffmpeg", ["-v", "error", "-xerror", "-i", info["path"], "-map", "0:a:0", "-f", "null", "-"]
    )
    return {
        "file": info,
        "integrated_lufs": float(loudness["input_i"]),
        "true_peak_dbtp": float(loudness["input_tp"]),
        "loudness_range_lu": float(loudness["input_lra"]),
        "decode_pass": True,
        "verification": "Measurement only; no perceptual listening approval.",
    }


def register_audio_tools(mcp):
    mcp.tool(name="fl_audio_probe")(probe)
    mcp.tool(name="fl_phrase_timing_plan")(timing_plan)
    mcp.tool(name="fl_prepare_audio_phrase")(prepare_phrase)
    mcp.tool(name="fl_measure_export")(measure)
    mcp.tool(name="fl_export_midi")(export_midi)
    mcp.tool(name="fl_delivery_copies")(delivery_copies)
    mcp.tool(name="fl_check_stems")(check_stems)


def export_midi(notes: list[dict], output: str, bpm: float = 126, ppq: int = 960) -> dict:
    """Write an editable MIDI arrangement using beats, pitch, velocity and channel.

    MIDI carries no instrument sound, plugin automation or audio. Import into FL
    after choosing instruments. Note-off precedes note-on at equal tick positions.
    """
    import mido

    timing_plan(bpm, bpm)
    path = Path(output).expanduser().resolve()
    if path.exists() or path.suffix.lower() not in (".mid", ".midi"):
        raise ValueError("Choose a new MIDI destination")
    if not 24 <= ppq <= 32767 or not notes:
        raise ValueError("Invalid PPQ or empty score")
    events = []
    for note in notes:
        pitch, channel = note["midi"], note.get("channel", 0)
        time, duration = note.get("time", 0), note["duration"]
        velocity = note.get("velocity", 0.8)
        if type(pitch) is not int or not 0 <= pitch <= 127:
            raise ValueError("Invalid MIDI pitch")
        if type(channel) is not int or not 0 <= channel <= 15:
            raise ValueError("Invalid MIDI channel")
        if not all(math.isfinite(x) for x in (time, duration, velocity)):
            raise ValueError("Note values must be finite")
        if time < 0 or duration * ppq < 1 or not 0 < velocity <= 1:
            raise ValueError("Invalid note timing or velocity")
        start, end = round(time * ppq), round((time + duration) * ppq)
        events.extend(
            [(start, 1, pitch, channel, max(1, round(velocity * 127))), (end, 0, pitch, channel, 0)]
        )
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm)))
    previous = 0
    for tick, on, pitch, channel, velocity in sorted(events):
        track.append(
            mido.Message(
                "note_on" if on else "note_off",
                note=pitch,
                channel=channel,
                velocity=velocity,
                time=tick - previous,
            )
        )
        previous = tick
    track.append(mido.MetaMessage("end_of_track", time=0))
    midi = mido.MidiFile(ticks_per_beat=ppq)
    midi.tracks.append(track)
    with path.open("xb") as handle:
        midi.save(file=handle)
    check = mido.MidiFile(path)
    return {
        "path": str(path),
        "notes": len(notes),
        "duration_seconds": check.length,
        "bpm": bpm,
        "ppq": ppq,
    }


def delivery_copies(
    master: str, wav_output: str, mp3_output: str, sample_rate: int = 48000
) -> dict:
    """Create 24-bit WAV and 320kbps MP3 from a finished master; measure both.

    This does not master, limit or normalize the mix. Recheck MP3 true peak for
    codec overshoot. Create the premaster by bypassing FL's final limiter first.
    """
    if sample_rate not in (44100, 48000, 88200, 96000):
        raise ValueError("Unsupported sample rate")
    source = probe(master)
    if source["audio"]["channels"] != 2:
        raise ValueError("Expected a stereo master")
    paths = [Path(x).expanduser().resolve() for x in (wav_output, mp3_output)]
    if (
        paths[0] == paths[1]
        or paths[0].suffix.lower() != ".wav"
        or paths[1].suffix.lower() != ".mp3"
    ):
        raise ValueError("Use separate WAV and MP3 destinations")
    if any(p.exists() for p in paths):
        raise ValueError("Destination exists; overwriting prohibited")
    for path, codec in zip(paths, [("pcm_s24le",), ("libmp3lame", "-b:a", "320k")]):
        run(
            "ffmpeg",
            [
                "-v",
                "error",
                "-n",
                "-i",
                source["path"],
                "-map",
                "0:a:0",
                "-ar",
                str(sample_rate),
                "-c:a",
                *codec,
                str(path),
            ],
        )
    results = [measure(str(p)) for p in paths]
    return {
        "exports": results,
        "peak_target_pass": all(x["true_peak_dbtp"] <= -1 for x in results),
        "note": "A failed peak target requires a new lower-peak master; no automatic gain changes.",
    }


def check_stems(paths: list[str]) -> dict:
    """Check common rate, channel count and sample duration of aligned stem files.

    Equal durations do not prove musical alignment. Export all stems from the
    same FL time selection including tails; listen and null-test where applicable.
    """
    if len(paths) < 2:
        raise ValueError("Provide at least two stems")
    files = [probe(p) for p in paths]
    rates = {x["audio"]["sample_rate"] for x in files}
    lengths = [float(x["audio"].get("duration", x["format"]["duration"])) for x in files]
    tolerance = 1 / min(int(x) for x in rates)
    return {
        "files": files,
        "same_rate": len(rates) == 1,
        "same_duration": max(lengths) - min(lengths) <= tolerance,
        "duration_spread_seconds": max(lengths) - min(lengths),
        "alignment": "Technical check only; musical alignment not established.",
    }
