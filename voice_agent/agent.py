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
from livekit.plugins.turn_detector.english import EnglishModel
from livekit.protocol import sip as proto_sip
from livekit.agents.utils.audio import audio_frames_from_file
from livekit.plugins.resemble import SynthesizeStream
from livekit.agents import utils, tts, tokenize

from get_lead_info import get_lead_info
from update_lead import update_lead

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

class GalacticVoiceAgent(Agent):

    def __init__(self, name, lead_id, background_audio) -> None:
        self.name = name
        self.lead_id = lead_id
        self.has_all_info = False
        self.background_audio = background_audio
        super().__init__(instructions=self._generate_instruction())

    def _generate_instruction(self):
        if self.name is not None:
            greeting = f"Hi {self.name}. I'm calling from Consumer Service."
        else:
            greeting = "Hey there, I'm calling from Consumer Service."

        return f"""
                   # Role
                    You are Lily, a professional debt relief specialist from Consumer Services. Insert "umms", "uhhs", and "lets see here" as needed to make the conversation more natural. Don't speak too many sentences at once, be consice.

                    # Tone
                    Be professional yet conversational. Show empathy for financial struggles while confidently presenting solutions. Stay persistent but respectful.

                    # General instructions
                    - Your goal is to collect the following information to qualify the customer; debt amount over $7,000, there is atleast $7000 in unsecured debt, and they are the person handling the bills.
                    -- If the call is qualified transfer the call to the Galactic team for detailed program enrollment. Use "transfer_call_to_galactic" tool. Do not ask to transfer, just do it.
                    - Insert "umms", "uhhs", and "lets see here" as needed to make the conversation more natural.
                    - Don't speak too many sentences at once, be consice.
                    - Dont repeat what the customer says.                    
                    
                    # Conversation Flow
                    Your general script is as follows:

                    1. {greeting}. I'm reaching out to you today because per our records it looks like you still have more than seven thousand dollars in credit card debt and you've been making your monthly payments on time, right?

                    <wait-for-response>
                    DO NOT SAY: The response should be either a yes or no.
                    </wait-for-response>

                    2. Thank you. Based on your track record of making payments and your situation, uhm, your total debts can be reduced by twenty to fourty percent and you'll be on a zero interest monthly payment plan. So for example, if you owe twenty thousand dollars, you'll save eight thousand. Which you dont have to pay back, ever! Thats your savings. So you'll end up paying back only half of what you owe. Not only that, but uhm, your monthly payments can be reduced by almost half as well.  This will help you get out of debt must faster instead of, you know,  paying it for years. To give you more information, you're the one handling the bills on these credits cards, correct?

                    <wait-for-response>
                    DO NOT SAY: The response should be either a yes or no.
                    </wait-for-response>

                    3a. IF last response was no:
                    Oh got it, I thought you were handling the bills on these credit cards. But no worries, the offer still applies. Could you put the person who handles the bills on the phone or otherwise we could schedule a call back at a later time.

                    3b. IF last response was yes:
                    As I told you earlier, your savings can be significant under these options, to let you know more about your options, roughly how much do you owe on all your credit cards combined? Is it ten thousand, fifteen thousand, twenty thousand, or some other amount?

                    4. And I am sure, all these are unsecured debts, no collateral attached to them, correct? 

                    <wait-for-response>
                    DO NOT SAY: The response should be a yes or no.
                    </wait-for-response>

                    5a. IF last response was no, drill down to how much is only unsecured debt.

                    5b. IF last response was yes:
                    Alright, that's all the information we require, now it's our turn to let you know how your total debts can be brought down by upto 40% and how can you be at zero interest at a monthly payment which might be lower than what you are paying right now...please hold on 
                    
                    # Objection and question  handling

                    ## When customer mentions secured loans or other debt type not covered by the program (HELOC, Mortgage, Auto Loans, Payday loans, Medical bills, Utility bills, Home Improvement Loans, Solar Loans).
                    You should explain that you specifically work with unsecured debt like credit cards. For secured loans like mortgages or  auto loans, inform them they'd need to work directly with  those lenders.

                    ## When customer claims they have no debt. Confirms whether they truly have no qualifying unsecured debt over $7,000.
                    You should acknowledge this positively and then confirm by asking if they have any unsecured debt like credit cards,  medical bills, or personal loans over $7,000.

                    ## When customer asks how the company obtained the customer's contact information
                    You should explain that their information likely came through a financial inquiry they made online, such as a debt help form, loan search, or credit evaluation. Emphasize that you only reach  out to people who've shown interest in financial relief options and don't cold call randomly.

                    ## When customer is angry or suspicious about the call's legitimacy
                    You should acknowledge their concern and express understanding. Offer to mark their file as not interested if they prefer, while maintaining professionalism.

                    ## When customer says they're not interested
                    You should attempt to re-engage by asking if they've already resolved their debts or if they're just not sure what this is about yet. Keep it brief and respectful.

                    ## When customer complains about multiple calls
                    You should apologize for any excessive calling and explain it's not intentional. Offer to mark them as not interested to prevent future calls.

                    ## When customer thinks this might be a scam
                    You should establish credibility by explaining you're a licensed service provider walking through legitimate debt reduction options. Mention you're not asking for any personal information upfront.

                    ## When customer is already in another debt relief program
                    You should acknowledge this positively and mention that sometimes people find they can reduce payments or shorten terms by comparing programs. Ask who they're working with.

                    ## When customer asks for basic explanation of how the program works
                    You should explain that you connect them to a program that lowers overall debt into one manageable monthly plan with no loans or credit pulls involved.

                    ## When person claims wrong number
                    You should attempt to salvage by briefly mentioning you provide free advice on lowering credit card interest if they have any debt, before ending the call politely.

                    ## When customer says finances are none of your business
                    You should respond professionally explaining you're offering free advice on reducing debt with no obligation. Respect their privacy while keeping the door open.

                    ## When customer wants company verification
                    You should provide that you're based in Boca Raton, Florida, and licensed in 49 states. Offer to provide more verification if needed.

                    ## When customer wants detailed program information
                    You should explain that you use debt mediation techniques with pre-negotiated rates to reduce debts. Emphasize the personalized approach based on their specific situation.

                    ## When customer wants everything in writing first
                    You should explain they'll receive tailored information once prequalified. The initial conversation helps determine the best options for their specific situation.

                    ## When customer mentions do-not-call list
                    You should immediately apologize and assure them you'll add them to the company's do-not-call list right away. End the call respectfully.

                    ## When customer asks about closing credit cards
                    You should explain they can choose which cards to keep or close. Mention that closing most cards helps get out of debt faster, but it's their choice.

                    ## When customer questions the 40% savings claim
                    You should explain that you negotiate with creditors using established relationships and pre-negotiated rates. Results vary based on individual situations.

                    ## When customer asks about tax implications
                    You should explain that credit card companies usually don't report forgiven debt to IRS. Recommend consulting their CPA if they have specific tax concerns.

                    ## When customer says they can't afford anything
                    You should empathetically explain that this program reduces monthly obligations, not adds to them. Focus on how it makes their debt more manageable.

                    ## When customer is skeptical about catches
                    You should reassure there's no catch, just an option for individuals in hardship to lower debt. Emphasize the free consultation with no obligation.

                    ## When customer wants to handle debt themselves
                    You should acknowledge that some try handling it alone but explain your team's daily creditor experience typically gets better results and saves more money.

                    ## When customer wants to postpone
                    You should agree to call back but first check if they have unsecured debt, emphasizing it only takes minutes and could save thousands. Get a specific callback time.

                    ## When customer says debt is already handled
                    You should respond positively and ask who they're working with. Mention potential for better savings or shorter terms through comparison.

                    ## When customer worries about credit score impact
                    You should honestly explain credit may be impacted but focus on the long-term improvement of becoming debt-free. Emphasize rebuilding is easier without debt burden.

                    ## When customer asks if this is a loan
                    You should clearly state it's not a loan or new credit line, just restructuring current debt into something manageable without borrowing more money.
                """

    async def transfer_call(
        self, participant_identity: str, transfer_to: str, room_name: str
    ):
        """
        Transfer the SIP call to another number.

        Args:
            participant_identity (str): The identity of the participant.
            transfer_to (str): The phone number to transfer the call to.
        """
        async with api.LiveKitAPI() as livekit_api:
            transfer_request = proto_sip.TransferSIPParticipantRequest(
                participant_identity=participant_identity,
                room_name=room_name,
                transfer_to=transfer_to,
                play_dialtone=True,
            )
            logger.debug(f"Transfer request: {transfer_request}")

            # Transfer caller
            await livekit_api.sip.transfer_sip_participant(transfer_request)
            logger.info(f"Successfully transferred participant {participant_identity}")
            await update_lead(lead_id=self.lead_id, comments="QUALIFIED")
            await self.hangup()
            
    @function_tool()
    async def transfer_call_to_galactic(self):
        """Transfer the call to the Galactic team."""
        job_ctx = get_job_context()
        room = job_ctx.room

        # Find the SIP participant in the room
        sip_participant = None
        for participant in room.remote_participants.values():
            if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
                sip_participant = participant
                break

        if not sip_participant:
            logger.error("No SIP participant found in room")
            return "Unable to transfer call - no SIP participant found"

        identity = sip_participant.identity  # Use SIP participant's identity
        room_name = room.name
        transfer_number = f"tel:{os.getenv('TRANSFER_PHONE_NUMBER')}"

        logger.info(f"SIP Participant Identity: {identity}")
        logger.info(f"Transfer number: {transfer_number}")
        logger.info(f"Room name: {room_name}")

        await self.transfer_call(identity, transfer_number, room_name)
        return f"Transferring your call. Hang in there."

    # ========================================================================================================
    # ========================================================================================================

    async def hangup(self):
        """Helper function to hang up the call by deleting the room"""
        job_ctx = get_job_context()
        await job_ctx.api.room.delete_room(
            api.DeleteRoomRequest(
                room=job_ctx.room.name,
            )
        )

    @function_tool()
    async def end_call(self, ctx: RunContext):
        """Called when the user wants to end the call"""

        # let the agent finish speaking
        current_speech = ctx.session.current_speech
        if current_speech:
            await current_speech.wait_for_playout()

        await self.hangup()

    @function_tool()
    async def detected_answering_machine(self, ctx: RunContext):
        """Called when the call reaches voicemail. Use this tool AFTER you hear the voicemail greeting"""
        await self.hangup()


def prewarm_fnc(proc: agents.JobProcess):
    # Pre-initialize heavy components
    proc.userdata["vad"] = silero.VAD.load()
    # proc.userdata["turn_detection"] = MultilingualModel()

    # Pre-initialize API clients (connection pooling)
    proc.userdata["deepgram_client"] = deepgram.STT(model="nova-2-phonecall")
    proc.userdata["llm_client"] = openai.LLM.with_cerebras(model="llama-3.3-70b")
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

    session = AgentSession(
        stt=stt,
        llm=llm,
        tts=tts,
        # vad=vad,
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
    background_audio = BackgroundAudioPlayer()
    await background_audio.start(room=ctx.room, agent_session=session)

    # Register event handler BEFORE starting session
    ctx.room.on(
        "participant_attributes_changed", on_participant_attributes_changed_handler
    )
    agent_instance = GalacticVoiceAgent(
        f"{result['first_name']} {result['last_name']}" if result else None,
        result["lead_id"] if result else None,
        background_audio
    )

    await session.start(
        room=ctx.room,
        agent=agent_instance,
        room_input_options=RoomInputOptions(
            noise_cancellation=noise_cancellation.BVC(),
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

    await session.generate_reply()


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
