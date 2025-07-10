import asyncio
import json
import logging
from dotenv import load_dotenv
import os
from livekit import agents, api, rtc
from livekit.agents import (
    AgentSession,
    Agent,
    BackgroundAudioPlayer,
    JobRequest,
    MetricsCollectedEvent,
    RoomInputOptions,
    RunContext,
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
from livekit.plugins.turn_detector.multilingual import MultilingualModel
from livekit.protocol import sip as proto_sip

from get_lead_info import get_lead_info
from update_lead import update_lead

load_dotenv(dotenv_path=".env.local")

logger = logging.getLogger("inbound-caller")
logger.setLevel(logging.DEBUG)

ENV = os.getenv("ENVIRONMENT")
IS_DEV = ENV == "development"

if IS_DEV:
    from metrics_csv_logger import MetricsCSVLogger



def prewarm_fnc(proc: agents.JobProcess):
    # Pre-initialize heavy components
    proc.userdata["vad"] = silero.VAD.load()
    # proc.userdata["turn_detection"] = MultilingualModel()

    # Pre-initialize API clients (connection pooling)
    proc.userdata["deepgram_client"] = deepgram.STT(model="nova-2-phonecall")
    proc.userdata["llm_client"] = google.LLM(
        model="gemini-2.5-flash-lite-preview-06-17",
    )
    proc.userdata["tts_client"] = resemble.TTS(
        api_key=os.getenv("RESEMBLE_API_KEY"),
        voice_uuid="332aece2",
    )

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

    vad = ctx.proc.userdata["vad"]
    turn_detection = MultilingualModel()
    stt = ctx.proc.userdata["deepgram_client"]
    llm = ctx.proc.userdata["llm_client"]
    tts = ctx.proc.userdata["tts_client"]

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
                elif call_status == "hangup":
                    logger.info("Call has been ended by a participant")
                    # Check if we have the agent instance and all questions answered
                    agent = getattr(session, "agent", None)
                    if agent is not None:
                        if getattr(agent, "has_all_info", False):
                            status = "QUALIFIED"
                        else:
                            status = "NOT_QUALIFIED"
                    else:
                        status = "NOT_QUALIFIED"

                    print(f"status: {status}")
                    await update_lead(
                        lead_id=result["lead_id"] if result else "",
                        comments=status,
                    )
                elif call_status == "ringing":
                    logger.info("Inbound call is now ringing for the caller")

    def on_participant_attributes_changed_handler(
        changed_attributes: dict, participant: rtc.Participant
    ):
        # Handle all participant attribute changes
        asyncio.create_task(
            handle_participant_attributes_changed(changed_attributes, participant)
        )
    #background_audio = BackgroundAudioPlayer()
    #await background_audio.start(room=ctx.room, agent_session=session)

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
            noise_cancellation=noise_cancellation.BVCTelephony(),
        ),
    )

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

    await ctx.connect()

    await session.generate_reply()


if __name__ == "__main__":
    agents.cli.run_app(
        agents.WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name="incoming-call-agent",
            load_threshold=0.75,
            prewarm_fnc=prewarm_fnc,
        )
    )
