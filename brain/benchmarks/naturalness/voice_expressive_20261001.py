"""Small, blind Eleven v4 Turbo voice pilot using MIST's real TTS adapter.

`--plan` is offline. `--run` is required before any paid synthesis request.
The pilot uses fixed words and a fixed order seed. It never retries synthesis.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import html
import json
import os
from pathlib import Path
import random
import sys
import time
import wave

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "brain"))
from duplex.tts import EXPRESSIVE_MODEL, FLASH_MODEL, StreamingTTS

OUTPUT = ROOT / "brain/results/v4-expressive-20261001/pilot"
MIST = "24AMj4dc02cYAwoUnqzN"
VOICES = {
    "MIST refined": MIST,
    "Jessica": "cgSgspJ2msm6clMCkdW9",
    "Laura": "FGY2WhTYpPnrIDTdsKH5",
    "Callum": "N2lVS1w4EtoT3dr4eOWO",
}
CASES = (
    {"id": "neutral", "label": "Neutral", "delivery": "neutral",
     "parts": ("The workshop opens at nine. The blue light is still on.",)},
    {"id": "empathy", "label": "Restrained empathy", "delivery": "reassuring",
     "parts": ("That sounds exhausting. We can take this one step at a time.",)},
    {"id": "curiosity", "label": "Curiosity", "delivery": "curious",
     "parts": ("Wait, how did the second sensor behave when the light changed?",)},
    {"id": "dry-humor", "label": "Dry humor", "delivery": "amused",
     "parts": ("Well, the robot has chosen a dramatic entrance.",)},
    {"id": "good-news", "label": "Good news", "delivery": "bright",
     "parts": ("That is wonderful news. I am so glad it worked!",)},
    {"id": "calm-urgency", "label": "Calm urgency", "delivery": "urgent",
     "parts": ("Stop the motors now. Keep your hands clear, then switch off the power.",)},
    {"id": "chunked-reply", "label": "Longer technical reply", "delivery": "neutral",
     "parts": ("I checked the three sensor readings. The front sensor reported a shorter distance than the side sensors. ",
               "That usually means the obstacle is ahead, but the reading could also be a reflection. ",
               "Keep the robot still while you check the raw values. If they stay consistent, turn slowly and test again.")},
)
FLASH_CASES = {"neutral", "empathy"}
ORDER_SEED = 20261001
CONNECT_TIMEOUT_S = 9.0
FINAL_TIMEOUT_S = 15.0
SAMPLE_RATE = 24000
BYTES_PER_SECOND = SAMPLE_RATE * 2
TAG_MARKERS = ("[warmly]", "[gently]", "[excited]", "[seriously]", "[curiously]",
               "[amused]", "[reassuringly]", "[calmly]")


def load_key() -> str | None:
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if key:
        return key
    for name in ("project.env", ".env"):
        path = ROOT / name
        if path.is_file():
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                if line.startswith("ELEVENLABS_API_KEY="):
                    value = line.partition("=")[2].strip()
                    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                        value = value[1:-1]
                    return value or None
    return None


def schedule() -> list[dict]:
    rng = random.Random(ORDER_SEED)
    rows = []
    for case in CASES:
        voices = list(VOICES.items())
        rng.shuffle(voices)
        for voice_name, voice_id in voices:
            rows.append({**case, "voice_name": voice_name, "voice_id": voice_id,
                         "model": EXPRESSIVE_MODEL})
        if case["id"] in FLASH_CASES:
            rows.append({**case, "voice_name": "MIST refined", "voice_id": MIST,
                         "model": FLASH_MODEL})
    return rows


def submitted_characters(rows: list[dict]) -> int:
    # Includes spoken words; provider control tags add a few more characters.
    return sum(len("".join(row["parts"])) for row in rows)


def plan() -> dict:
    rows = schedule()
    return {"samples": len(rows), "spoken_characters": submitted_characters(rows),
            "maximum_budget_characters": 5000, "order_seed": ORDER_SEED,
            "watchdog_idle_seconds": 10, "final_deadline_seconds": FINAL_TIMEOUT_S,
            "cases": [{"id": row["id"], "voice": row["voice_name"], "model": row["model"],
                       "delivery": row["delivery"], "characters": len("".join(row["parts"]))}
                      for row in rows]}


def write_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm)


async def render_one(key: str, sample: dict, clip_id: str, audio_dir: Path) -> dict:
    started = time.monotonic()
    first_text_at = first_audio_at = None
    metric = style = None
    chunks: list[bytes] = []
    warnings: list[str] = []
    alignment_bytes: dict[str, int] = {}
    caption_bytes: dict[str, int] = {}
    mouth_count = caption_count = 0
    caption_last = ""
    caption_tag_leak = False

    async def emit(event: dict) -> None:
        nonlocal metric, style, first_audio_at, mouth_count, caption_count, caption_last, caption_tag_leak
        kind = event.get("type")
        if kind == "audio":
            if first_audio_at is None:
                first_audio_at = time.monotonic()
            raw = base64.b64decode(event["pcm"], validate=True)
            chunks.append(raw)
            source = str(event.get("alignment_source", "unavailable"))
            alignment_bytes[source] = alignment_bytes.get(source, 0) + len(raw)
            source = str(event.get("caption_source", "unavailable"))
            caption_bytes[source] = caption_bytes.get(source, 0) + len(raw)
            mouth_count += len(event.get("mouth_cues", ()))
            captions = event.get("caption_cues", ())
            caption_count += len(captions)
            if captions:
                caption_last = str(captions[-1].get("text", caption_last))
                caption_tag_leak |= any("[" in str(cue.get("text", "")) or "]" in str(cue.get("text", ""))
                                        for cue in captions)
        elif kind == "latency":
            metric = event.get("tts")
        elif kind == "speech_style":
            style = {k: event.get(k) for k in ("delivery", "tag_name", "voice_id", "model", "phase")}
        elif kind == "voice_warning":
            warnings.append(str(event.get("code", "unknown")))

    result = {"clip_id": clip_id, "case_id": sample["id"], "voice_name": sample["voice_name"],
              "voice_id": sample["voice_id"], "model": sample["model"],
              "delivery": sample["delivery"], "text": "".join(sample["parts"]),
              "parts": list(sample["parts"]), "status": "not_started"}
    tts = StreamingTTS(key, emit, model_id=sample["model"], voice_id=sample["voice_id"])
    context = None
    try:
        try:
            await asyncio.wait_for(tts.start(), CONNECT_TIMEOUT_S)
            result["cold_connect_s"] = time.monotonic() - started
        except Exception as error:
            result.update(status="connect_failed", error_type=type(error).__name__)
            return result
        await tts.begin(clip_id, delivery=sample["delivery"])
        context = tts.active
        if context is None or tts.muted:
            result["status"] = "begin_failed"
            return result
        first_text_at = time.monotonic()
        for part in sample["parts"]:
            await tts.text(part)
        await tts.finish(result["text"])
        deadline = time.monotonic() + FINAL_TIMEOUT_S
        while time.monotonic() < deadline:
            if warnings or tts.muted:
                result["status"] = "provider_error"
                break
            if metric is not None and context not in tts.pending:
                result["status"] = "received"
                break
            await asyncio.sleep(.02)
        else:
            result["status"] = "final_or_drain_timeout"
    except Exception as error:
        result.update(status="send_or_receive_failed", error_type=type(error).__name__)
    finally:
        pcm = b"".join(chunks)
        audio_bytes = len(pcm)
        metric_duration = metric.get("output_s") if isinstance(metric, dict) else None
        equivalent = (isinstance(metric_duration, (int, float)) and
                      abs(metric_duration - audio_bytes / BYTES_PER_SECOND) <= 1 / SAMPLE_RATE)
        tag_leak = caption_tag_leak
        complete = (result["status"] == "received" and metric is not None and
                    context not in tts.pending and audio_bytes > 0 and not warnings and
                    equivalent and not tag_leak)
        if result["status"] == "received":
            result["status"] = "ok" if complete else "incomplete_or_tag_leak"
        if pcm:
            write_wav(audio_dir / f"{clip_id}.wav", pcm)
        result.update(
            provider_final=metric is not None, pending_drained=context not in tts.pending,
            audio_bytes=audio_bytes, audio_duration_s=audio_bytes / BYTES_PER_SECOND,
            metric_output_s=metric_duration, metric_duration_matches_pcm=bool(equivalent),
            first_text_to_first_audio_s=(None if first_text_at is None or first_audio_at is None
                                         else first_audio_at - first_text_at),
            warnings=warnings, alignment_source_bytes=alignment_bytes,
            caption_source_bytes=caption_bytes, mouth_cue_count=mouth_count,
            caption_cue_count=caption_count, caption_final_text=caption_last,
            caption_tag_leak=tag_leak,
            caption_character_coverage=(len(caption_last) / len(result["text"]) if result["text"] else 0),
            speech_style=style,
            elapsed_s=time.monotonic() - started,
            wav_file=f"{clip_id}.wav" if pcm else None)
        await tts.close()
    return result


def write_listen_page(rows: list[dict], path: Path) -> None:
    lines = []
    for row in rows:
        audio = (f'<audio controls preload="none" src="audio/{html.escape(row["wav_file"], quote=True)}"></audio>'
                 if row.get("wav_file") else "Audio unavailable")
        expected = html.escape(row.get("text", ""))
        voice = html.escape(row.get("voice_name", ""))
        model = html.escape(row.get("model", ""))
        latency = row.get("first_text_to_first_audio_s")
        latency_label = "" if latency is None else f"{latency * 1000:.0f} ms"
        lines.append(f'<tr><td>{html.escape(row["clip_id"])}</td><td>{html.escape(row["case_id"])}</td>'
                     f'<td>{audio}<details><summary>Expected words</summary><p>{expected}</p></details></td>'
                     f'<td class="reveal">{voice}<br><small>{model}</small></td>'
                     f'<td class="reveal">{latency_label}</td>'
                     f'<td><select class="fit" aria-label="Emotion fit for {html.escape(row["clip_id"])}">'
                     '<option value="">Rate</option><option value="1">1</option><option value="2">2</option>'
                     '<option value="3">3</option><option value="4">4</option><option value="5">5</option>'
                     '</select></td><td><select class="naturalness" aria-label="Naturalness">'
                     '<option value="">Rate</option><option value="1">1</option><option value="2">2</option>'
                     '<option value="3">3</option><option value="4">4</option><option value="5">5</option>'
                     '</select></td><td><label><input class="overacting" type="checkbox"> Yes</label></td>'
                     '<td><input class="notes" aria-label="Notes" placeholder="Tags, missing words, artifacts"></td></tr>')
    page = ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            '<title>MIST voice listening pilot</title><style>body{font:16px/1.5 system-ui;max-width:1300px;margin:2rem auto;padding:0 1rem;color:#263832;background:#f6f6f0}table{width:100%;border-collapse:collapse;background:white}th,td{text-align:left;padding:.55rem;border-bottom:1px solid #d9e0d8;vertical-align:top}audio{max-width:190px}input.notes{width:100%;min-width:130px;box-sizing:border-box}details p{max-width:25rem;margin:.3rem 0}.reveal{display:none}body.revealed .reveal{display:table-cell}button{margin:.5rem .5rem .8rem 0}</style>'
            '<h1>MIST voice listening pilot</h1><p>Listen first, then score emotion fit and naturalness from 1 to 5. Open the expected words to check omissions. Mark overacting and note tags or audio faults.</p>'
            '<button id="export">Export ratings</button><button id="reveal">Reveal voices and timing</button>'
            '<table><thead><tr><th>Clip</th><th>Case</th><th>Audio and words</th><th class="reveal">Voice</th><th class="reveal">First audio</th><th>Fit</th><th>Naturalness</th><th>Overacting?</th><th>Notes</th></tr></thead><tbody>'
            + ''.join(lines) + '</tbody></table>'
            '<script>const key="mist-v4-pilot-ratings";let saved={};try{saved=JSON.parse(localStorage.getItem(key)||"{}")}catch{}'
            'document.querySelectorAll("tbody tr").forEach(tr=>{const id=tr.cells[0].textContent,f=tr.querySelector(".fit"),n=tr.querySelector(".naturalness"),o=tr.querySelector(".overacting"),notes=tr.querySelector(".notes");f.value=saved[id]?.fit||"";n.value=saved[id]?.naturalness||"";o.checked=!!saved[id]?.overacting;notes.value=saved[id]?.notes||"";const save=()=>{saved[id]={fit:f.value,naturalness:n.value,overacting:o.checked,notes:notes.value};localStorage.setItem(key,JSON.stringify(saved))};f.onchange=save;n.onchange=save;o.onchange=save;notes.oninput=save});'
            'document.getElementById("reveal").onclick=()=>document.body.classList.add("revealed");'
            'document.getElementById("export").onclick=()=>{const blob=new Blob([JSON.stringify(saved,null,2)],{type:"application/json"}),a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download="ratings.json";a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)};</script></html>')
    path.write_text(page, encoding="utf-8")


async def run(key: str, output_dir: Path) -> dict:
    rows = schedule()
    audio_dir = output_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=False)
    results = {"method": "Fixed 30 clip pilot; v4 across four voices, plus two MIST Flash controls; no synthesis retries.",
               "plan": plan(), "samples": []}
    result_path = output_dir / "results.json"
    result_path.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    for index, sample in enumerate(rows):
        row = await render_one(key, sample, f"clip-{index + 1:02d}", audio_dir)
        results["samples"].append(row)
        result_path.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"clip": row["clip_id"], "status": row["status"]}), flush=True)
    write_listen_page(results["samples"], output_dir / "listen.html")
    return {"samples": len(rows), "failures": sum(r["status"] != "ok" for r in results["samples"]),
            "results": str(result_path), "listen_page": str(output_dir / "listen.html")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--plan", action="store_true", help="show frozen order without loading a key")
    action.add_argument("--run", action="store_true", help="run paid ElevenLabs synthesis")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.plan:
        print(json.dumps(plan(), indent=2))
        return 0
    if submitted_characters(schedule()) >= 5000:
        raise SystemExit("Pilot exceeds the 5,000-character guard.")
    if args.output_dir.exists():
        raise SystemExit(f"Refusing to replace an existing output directory: {args.output_dir}")
    key = load_key()
    if not key:
        raise SystemExit("ElevenLabs key was not found in the local environment.")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    print(json.dumps(asyncio.run(run(key, args.output_dir))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
