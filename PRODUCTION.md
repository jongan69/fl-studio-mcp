# FL Studio production bridge

Maintained fork of karl-andres/fl-studio-mcp, preserving its MIT license and attribution.
Designed to support an editable remix workflow, with explicit readbacks and technical QC.
The tool cannot judge whether a song sounds professionally finished.

## Install from this checkout

```sh
uv sync --group dev
uv run python scripts/install_production.py
codex mcp add fl-studio -- /absolute/path/to/checkout/.venv/bin/fl-studio-mcp
```

Enable a **dedicated** virtual MIDI port named `FLStudioMCP`; select this controller
and enable that input in FL MIDI settings. A different port can be chosen by setting
`FL_MCP_MIDI_PORT` to its exact name. No arbitrary MIDI-device fallback is allowed.
Reload the script in FL's Script output window after updates. Both ends must be updated:
old controllers lack response IDs and will intentionally fail the new handshake.
Install FFmpeg and FFprobe on PATH for offline audio tools. No hosted AI or API key needed.
The installer backs up existing FL scripts and verifies installed SHA-256 hashes.

## Implemented and checked

| Capability | Status / limits |
| --- | --- |
| Dedicated MIDI connection and handshake | Live FL 2026 response verified, not just port-open status |
| Request correlation and mailbox serialization | Stale response rejection; thread + process lock; duplicate most-recent request suppression |
| Error propagation | Unsupported/error responses are failures; timeout means outcome unknown, no blind mutation retry |
| Tempo read/write | Live 126 BPM verified; FL API uses milli-BPM; finite 40–240 guard |
| Native pattern creation/naming/select/list | Live seven-pattern rebuild readback; existing notes preserved |
| Channel volume and pan | API argument order corrected; live normalized volume readback verified |
| Native mixer routes | Generic route tool, graph feedback prevention, live send readback |
| Existing plugin parameters, transport, channel rack | Inherited upstream; not every plugin has been verified |
| Persistent piano roll note scripts | Entire queue preflight before clear; note errors surfaced; GUI trigger still required; modified validation unit-tested, not yet live-auditioned |
| Musical phrase preparation | Explicit measured BPM/downbeat inputs; exact beat count; non-overwriting pitch-preserving FFmpeg preview |
| MIDI arrangement export | Editable notes, beat times, lengths, velocity and MIDI channels; round-trip test |
| Audio inspection and provenance | Codec/rate/channel metadata and SHA-256 |
| Export measurements | Full decode, integrated LUFS, true peak and LRA; no listening claim |
| Delivery encoding | Non-overwriting 24-bit stereo WAV and 320kbps MP3 from an already finished master; both measured |
| Stem QC | Common sample rate and duration check; does not prove musical alignment |

The controller's response writer uses normal file writes because FL 26's embedded
Python rejects `os.replace`. The reader retries incomplete JSON and only accepts a
matching request ID. Server-side command writes use atomic replacement. Duplicate
suppression is in-memory for the last request, not durable exactly-once execution.
A process crash during a mutation requires inspecting FL before retrying.

Piano roll tools default to `auto_trigger=False` to prevent unverified focus from
causing keystrokes in another application. The inherited optional keyboard trigger
is not used by the production validation. Queueing notes is not proof they exist
inside the intended pattern/channel. Inspect focus and read back the actual score.
Do not concurrently enqueue notes while the FL piano script is processing.

## Remaining work before unattended remix production

These are real gaps, not completed features:

- Native audio import, playlist clip placement and time-warp marker editing.
- Plugin loading, preset-state capture and reliable plugin-specific automation.
- Correlated piano transactions with target pattern/channel identity, rollback and
  native confirmation; existing script validation prevents malformed-queue clears
  but cannot guarantee recovery from an FL error mid-edit.
- Full native session snapshot/save/reopen validation and native render orchestration.
- Automated stem export with tails and routing compensation, premaster bypass state,
  null/reconstruction tests, mono cancellation and edit-click inspection.
- Beat/downbeat confidence and variable-tempo maps; harmony/key analysis with
  evidence. `fl_phrase_timing_plan` calculates a plan, it does not estimate tempo.
- Critical listening of sources, separated stems, phrase alignment and finished mix.

Image-Line's MIDI and piano APIs do not expose a complete general-purpose DAW API.
The missing operations need tested native UI adapters or a version-aware FLP writer;
there is no arbitrary code-execution endpoint or fabricated render-success response.
The current native UI automation reports no usable window for FL main-window
coordinate editing, so the native clip-placement adapter is not validated yet.

## Production workflow and acceptance gates

1. Preserve source/stems; record hashes and source codec. Lossy source decoded to
   WAV is still lossy in provenance.
2. Verify source BPM, downbeats and full vocal phrases against a click, testing
   half/double-time. For a measured 92 BPM source at 126, use 126/92 speed for
   beat-for-beat alignment. 126/(92*4/3) does not align source and house beats.
3. Approve an eight-bar vocal/drum loop by listening before a full arrangement.
   Audition timestretch and stem artifacts; use cleaner phrases where necessary.
4. Build 8/16/32-bar sections around the hook, with contrast and a stronger second
   drop. Keep kick/sub separation, vocal clarity and musical phrase pickups.
5. Mix first. Export premaster without final limiting; export master and aligned
   stems from the same project range with full tails. Never manufacture a
   premaster by turning down a limited master.
6. Measure master and MP3 separately; aim roughly -10 to -8 LUFS and <= -1 dBTP
   only if punch remains intact. Do not normalize automatically to pass a number.
7. Decode, reopen project, verify assets, listen continuously and report actual
   measured results. Technical checks do not equal artistic approval.

## Validation

```sh
uv run pytest -q
uv run ruff check src/fl_studio_mcp/tools/audio.py src/fl_studio_mcp/utils/midi_connection.py tests
```

Tests exercise channel API signatures, milli-BPM, duplicate triggers, stale response
rejection, feedback refusal, unsafe MIDI-port fallback, invalid replacement queue
preservation, source-to-target phrase duration, actual FFmpeg encodes and decode,
MIDI roundtrip, delivery bit depth/bitrate, and overwrite refusal. Live readback
was tested on FL Studio 26.1.7.5421, macOS Apple Silicon, scripting API 45.
Windows lock implementation is present but not live-tested.
