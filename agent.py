from dotenv import load_dotenv
import os

from livekit import agents
from livekit.agents import AgentSession, RoomInputOptions
from livekit.plugins import (
    deepgram,
    noise_cancellation,
    silero,
    resemble,
    google,
    openai,
)
from livekit.plugins.turn_detector.multilingual import MultilingualModel
from livekit.plugins.turn_detector.english import EnglishModel
from galactic_agent import GalacticAgent

load_dotenv()

# Override the Resemble WebSocket URL to use the galactic endpoint
import livekit.plugins.resemble.tts as resemble_tts
resemble_tts.RESEMBLE_WEBSOCKET_URL = "wss://galactic-ws.cluster.resemble.ai/stream"

# Monkey patch to add SSML exaggeration to Resemble TTS
import asyncio
import json
import base64
import aiohttp
from livekit.agents import utils, tts, tokenize
from livekit.plugins.resemble import SynthesizeStream

# Store the original _run_ws method
original_run_ws = SynthesizeStream._run_ws

async def patched_run_ws(self, input_stream: tokenize.SentenceStream, output_emitter: tts.AudioEmitter) -> None:
    segment_id = utils.shortuuid()
    output_emitter.start_segment(segment_id=segment_id)

    last_index = 0
    input_ended = False

    async def _send_task(ws: aiohttp.ClientWebSocketResponse) -> None:
        nonlocal input_ended, last_index
        async for data in input_stream:
            last_index += 1
            payload = {
                "voice_uuid": self._opts.voice_uuid,
                "data": f"<speak exaggeration='0.72'>{data.token}</speak>",  # Modified line
                "request_id": last_index,
                "sample_rate": self._opts.sample_rate,
                "precision": "PCM_16",
                "output_format": "mp3",
            }
            self._mark_started()
            await ws.send_str(json.dumps(payload))

        input_ended = True

    async def _recv_task(ws: aiohttp.ClientWebSocketResponse) -> None:
        while True:
            msg = await ws.receive()
            if msg.type in (
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSING,
            ):
                raise tts.APIStatusError("Resemble connection closed unexpectedly")

            if msg.type != aiohttp.WSMsgType.TEXT:
                # logger.warning("Unexpected Resemble message type %s", msg.type)
                continue

            data = json.loads(msg.data)
            if data.get("type") == "audio":
                if data.get("audio_content", None):
                    b64data = base64.b64decode(data["audio_content"])
                    output_emitter.push(b64data)

            elif data.get("type") == "audio_end":
                index = data["request_id"]
                if index == last_index and input_ended:
                    output_emitter.end_segment()
                    break
            else:
                # logger.error("Unexpected Resemble message %s", data)
                pass

    async with self._tts._pool.connection(timeout=self._conn_options.timeout) as ws:
        tasks = [
            asyncio.create_task(_send_task(ws)),
            asyncio.create_task(_recv_task(ws)),
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            await utils.aio.gracefully_cancel(*tasks)

# Apply the monkey patch
SynthesizeStream._run_ws = patched_run_ws

def prewarm_fnc(proc: agents.JobProcess):
    # proc.userdata["stt"] = deepgram.STT(model="nova-2-phonecall")
    # proc.userdata["llm"] = google.LLM(model="gemini-2.5-flash-lite-preview-06-17")
    # proc.userdata["tts"] = resemble.TTS(api_key=os.getenv("RESEMBLE_API_KEY"), voice_uuid="3c089e29")
    #proc.userdata["vad"] = silero.VAD.load()
    #proc.userdata["turn_detection"] = MultilingualModel()
    pass

async def entrypoint(ctx: agents.JobContext):
    await ctx.connect()

    session = AgentSession(
        stt=deepgram.STT(model="nova-2-phonecall"),
        #llm=google.LLM(model="gemini-2.5-flash-lite-preview-06-17"),
        llm=openai.LLM.with_cerebras(model="llama-3.3-70b",),
        tts=resemble.TTS(api_key=os.getenv("RESEMBLE_API_KEY"), voice_uuid="3c089e29", sample_rate=24000),
        #vad=silero.VAD.load(),
        turn_detection=EnglishModel()
    )

    await session.start(
        room=ctx.room,
        agent=GalacticAgent(),
        room_input_options=RoomInputOptions(
            noise_cancellation=noise_cancellation.BVCTelephony(), 
        ),
    )

    await session.generate_reply()

if __name__ == "__main__":
    agents.cli.run_app(
        agents.WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name="incoming-call-agent"
            #prewarm_fnc=prewarm_fnc
        )
    )