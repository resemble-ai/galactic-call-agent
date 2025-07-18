import asyncio
import base64
import json
import logging
import aiohttp
from dotenv import load_dotenv
import os
from livekit import agents, api, rtc
from livekit.agents import (
    AgentSession,
    MetricsCollectedEvent,
    RoomInputOptions,
    UserStateChangedEvent,
    function_tool,
    get_job_context,
    metrics,
)
from livekit.plugins import (
    openai,
    cartesia,
    deepgram,
    noise_cancellation,
    silero,
    resemble,
    elevenlabs,
    google,
)
from livekit.plugins.turn_detector.english import EnglishModel
from livekit.protocol import sip as proto_sip
from livekit.plugins.resemble import SynthesizeStream
from livekit.agents import utils, tts, tokenize

from apis.get_lead_info import get_lead_info
from status_codes import DISPOSITION_DEAD_AIR, DISPOSITION_DEBT_7K_10K_HANGUP, DISPOSITION_DEBT_OVER_10K_HANGUP, DISPOSITION_IMMEDIATE_HANGUP, DISPOSITION_QUALIFIED_NOT_TRANSFERRED
from GalacticVoiceAgent.agent import GalacticVoiceAgent

load_dotenv(dotenv_path=".env.local")

logger = logging.getLogger("inbound-caller")
logger.setLevel(logging.DEBUG)

ENV = os.getenv("ENVIRONMENT")
IS_DEV = ENV == "development"

if IS_DEV:
    from metrics_csv_logger import MetricsCSVLogger
    
# Override the Resemble WebSocket URL to use the galactic endpoint
import livekit.plugins.resemble.tts as resemble_tts
resemble_tts.RESEMBLE_WEBSOCKET_URL = "wss://galactic-ws.cluster.resemble.ai/stream"

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
                "data": f"<speak exaggeration='0.7'>{data.token}</speak>",  # Modified line
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
                raise RuntimeError("Resemble connection closed unexpectedly")

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
    # Pre-initialize heavy components
    proc.userdata["vad"] = silero.VAD.load()

    # Pre-initialize API clients (connection pooling)
    proc.userdata["deepgram_client"] = deepgram.STT(model="nova-2-phonecall")
    proc.userdata["llm_client"] = openai.LLM.with_cerebras(model="llama-3.3-70b", temperature=0.1)
    
    proc.userdata["tts_client"] = resemble.TTS(api_key=os.getenv("RESEMBLE_API_KEY"), voice_uuid="3c089e29", sample_rate=24000)
    # proc.userdata["tts_client"] = cartesia.TTS(
    #     api_key=os.getenv("CARTESIA_API_KEY"),
    #     voice="f786b574-daa5-4673-aa0c-cbe3e8534c02",
    # )
    # proc.userdata["tts_client"] = elevenlabs.TTS(
    #     api_key="sk_e09e83bf20fd499d5b983625b670e9bb6484ea3b4da70f1e",
    #     voice_id="NwhlWbOasPHy5FAy7b7U",
    # )


async def entrypoint(ctx: agents.JobContext):
    phone_number = None
    await ctx.connect()

    # Wait for a SIP participant to join
    try:
        sip_participant = await ctx.wait_for_participant(
            kind=rtc.ParticipantKind.PARTICIPANT_KIND_SIP
        )

        if sip_participant.attributes:
            # For Twilio SIP trunking, the phone number is in 'sip.phoneNumber'
            phone_number = sip_participant.attributes.get("sip.phoneNumber")

            if phone_number:
                # Clean up the phone number (remove + if needed for API)
                phone_number = "8052226101" if IS_DEV else phone_number.strip()
                result = await get_lead_info(phone_number)
                logger.info(f"Result: {result}")
            else:
                logger.warning("sip.phoneNumber not found in attributes")
                logger.info(
                    f"Available attribute fields: {list(sip_participant.attributes.keys())}"
                )

        if phone_number:
            logger.info(f"Ready to fetch lead info for: {phone_number}")

    except asyncio.TimeoutError:
        logger.error("Timeout waiting for SIP participant")
    except Exception as e:
        logger.error(f"Error waiting for participant: {e}")

    # vad = ctx.proc.userdata["vad"]
    turn_detection = EnglishModel()
    stt = ctx.proc.userdata["deepgram_client"]
    llm = ctx.proc.userdata["llm_client"]
    tts = ctx.proc.userdata["tts_client"]
    vad = ctx.proc.userdata["vad"]

    session = AgentSession(
        stt=stt,
        llm=llm,
        tts=tts,
        vad=vad,
        turn_detection=turn_detection,
    )

    async def handle_participant_attributes_changed(
        changed_attributes: dict, participant: rtc.Participant
    ):
        logger.info(
            f"Participant {participant.identity} attributes changed: {changed_attributes}"
        )

        # Check if this is a SIP participant and if call status has changed
        if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
            # Check if sip.callStatus is in the changed attributes
            if "sip.callStatus" in changed_attributes:
                call_status = changed_attributes["sip.callStatus"]
                logger.info(f"SIP Call Status updated: {call_status}")
                # Log specific call status information
                if call_status == "active":
                    logger.info("Call is now active and connected")
                elif call_status == "automation":
                    logger.info("Call is now connected and dialing DTMF numbers")
                elif call_status == "dialing":
                    logger.info("Call is now dialing and waiting to be picked up")
                elif call_status == "ringing":
                    logger.info("Inbound call is now ringing for the caller")
                elif call_status == "hangup":
                    logger.info("Call has been ended by a participant")
                    chat_ctx = agent_instance.chat_ctx.copy()
                    chat_ctx.add_message(role="user", content="State only the numeric value of the unsecured debt amount customer has without any currency symbols or words. Just the number. If you cannot find return 0")
                    if agent_instance.current_status != DISPOSITION_QUALIFIED_NOT_TRANSFERRED:
                        await agent_instance.update_chat_ctx(chat_ctx)

                        response_stream = llm.chat(chat_ctx=chat_ctx)
                        unsecured_debt_amount = ""

                        async for chunk in response_stream:
                            if chunk.delta and chunk.delta.content:
                                unsecured_debt_amount += chunk.delta.content
                        
                        try:
                            unsecured_debt_amount = int(unsecured_debt_amount)
                        except:
                            raise TypeError("Debt amount is not a string")
                        
                        print(f"Debt amount: {unsecured_debt_amount}")
                        
                        if unsecured_debt_amount>10_000:
                            agent_instance.current_status = DISPOSITION_DEBT_OVER_10K_HANGUP
                        elif unsecured_debt_amount>7_000:
                            agent_instance.current_status = DISPOSITION_DEBT_7K_10K_HANGUP
                        else:
                            agent_instance.current_status = DISPOSITION_IMMEDIATE_HANGUP
                        
                        print(f"Agent status: {agent_instance.current_status}")
                        await agent_instance.hangup()
                    
                        
    def on_participant_attributes_changed_handler(
        changed_attributes: dict, participant: rtc.Participant
    ):
        # Handle all participant attribute changes
        asyncio.create_task(
            handle_participant_attributes_changed(changed_attributes, participant)
        )


    # Register event handler BEFORE starting session
    ctx.room.on(
        "participant_attributes_changed", on_participant_attributes_changed_handler
    )
    agent_instance = GalacticVoiceAgent(
        f"{result['first_name']} {result['last_name']}" if result else None,
        result["lead_id"] if result else None
    )

    await session.start(
        room=ctx.room,
        agent=agent_instance,
        room_input_options=RoomInputOptions(
            noise_cancellation=noise_cancellation.BVC(),
        ),
    )

    inactivity_task: asyncio.Task | None = None
    async def user_presence_task():
        try:
            await asyncio.sleep(10)
            agent_instance.current_status = DISPOSITION_DEAD_AIR
            await agent_instance.hangup()
        except asyncio.CancelledError:
            print("Inactivity task cancelled - user returned")
            return

    @session.on("user_state_changed")
    def _user_state_changed(ev: UserStateChangedEvent):
        print(f"User status events: {ev.new_state}")
        nonlocal inactivity_task
        
        print(f"New State: {ev.new_state}")
        
        if ev.new_state == "away":
            # Cancel existing task
            if inactivity_task and not inactivity_task.done():
                inactivity_task.cancel()
            
            # Schedule async reply generation
            async def handle_away():
                await session.generate_reply(
                    instructions="The user has been inactive. Politely check if the user is still present."
                )
                nonlocal inactivity_task
                inactivity_task = asyncio.create_task(user_presence_task())
            
            asyncio.create_task(handle_away())
            
        elif inactivity_task is not None:
            if inactivity_task is not None and not inactivity_task.done():
                inactivity_task.cancel()
            inactivity_task = None
            print("User is listening - cancelled hangup")
            
    # Store reference to agent for access in event handlers
    setattr(session, "agent", agent_instance)
    usage_collector = metrics.UsageCollector()
    
    if IS_DEV:
        csv_logger = MetricsCSVLogger()
        csv_filename = csv_logger.get_csv_filename("llm_provider", "llm_model")
        csv_logger.initialize_csv(csv_filename)
        logger.info(f"Metrics will be logged to: {csv_filename}")

    @session.on("metrics_collected")
    def _on_metrics_collected(ev: MetricsCollectedEvent):
        # Log the raw metric for debugging
        logger.info(f"Metrics: {ev.metrics}")

        # Collect for summary
        usage_collector.collect(ev.metrics)

        if IS_DEV:
            asyncio.create_task(
                asyncio.to_thread(csv_logger.write_metrics, csv_filename, ev.metrics)
            )

    async def log_usage():
        summary = usage_collector.get_summary()
        logger.error(f"Usage: {summary}")

    ctx.add_shutdown_callback(log_usage)

    await session.generate_reply(allow_interruptions=False)


if __name__ == "__main__":
    agents.cli.run_app(
        agents.WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name="incoming-call-agent",
            load_threshold=0.75,
            # num_idle_processes=10,
            prewarm_fnc=prewarm_fnc,
        )
    )
