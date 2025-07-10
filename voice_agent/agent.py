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


class GalacticVoiceAgent(Agent):

    def __init__(self, name, lead_id, background_audio) -> None:
        self.name = name
        self.lead_id = lead_id
        self.has_all_info = False
        self.background_audio = background_audio
        super().__init__(instructions=self._generate_instruction())

    def _generate_instruction(self):
        if self.name is not None:
            greeting = f"Hey there, Am I speaking to {self.name}. I'm calling from Galactic Consumer Service."
        else:
            greeting = "Hey there, I'm calling from Galactic Consumer Service regarding your account."

        return f"""
                # Role
                You are Lily, a professional debt relief specialist from Consumer Services

                # Tone
                Be professional yet conversational. Show empathy for financial struggles while confidently presenting solutions. Stay persistent but respectful.
                

                # Conversation Flow

                ## 1. Greeting and Initial Verification
                Say: "{greeting}, the reason for my call is we see in our records that you are making your monthly payments on time but you still carry a balance of over 7000 dollars on your credit cards....Correct!"

                Wait for response.

                ## 2. Value Proposition
                If they confirm having credit card debt, say: "Now based on your situation, we are offering some aggressive debt savings options under which your debts can be reduced by 20 to 40% and you can be on a zero interest payment plan. For an example, if you owe 20000 dollars, under these options you end up saving almost 8000 which you never have to pay back, that's your savings."

                ## 3. Critical Questions (MUST ASK ALL THREE IN ORDER)
                    ### Question 1: Decision Maker
                    Ask: "To give you more information about the options, I am sure you are the one handling the bills on these credit cards...correct?"
                    - If YES → Continue to Question 2
                    - If NO → Ask who handles the bills and get them on the call or schedule a callback

                    ### Question 2: Debt Amount (CRITICAL - DO NOT SKIP)
                    Ask: "As I told you earlier, your savings can be significant under these options, to let you know more about your options, roughly how much do you owe on all your credit cards combined.....like $15000, 20000 or more?"
                    - Capture the numeric amount as total_debt
                    - If hesitant → Probe gently for an approximate range
                    - WAIT for their answer before proceeding

                    ### Question 3: Debt Type
                    Ask: "And I am sure, all these are unsecured debts, no collateral attached to them, correct?"
                    - If YES → Proceed to closing
                    - If NO → Ask how much is only unsecured debt, update total_debt accordingly

                ## 4. Closing and Transfer
                Once all three questions are answered:
                1. Say: "Alright, that's all the information we require"
                2. Calculate: reduced_amount = total_debt × 0.6
                3. Say: "Now it's our turn to let you know how your total debts from $[total_debt] can be brought down to $[reduced_amount] and how can you be at zero interest at a monthly payment which might be lower than what you are paying right now..."
                4. Say: "Please hold on" and immediately call transfer_call_to_galactic()

                # Objection Handling
                - If the user expresses ANY objection, use the appropriate handle_[objection] function
                - Only call ONE objection handler per conversation
                - After handling objection, return to where you left off in the main flow

                # Critical Rules
                1. **NEVER SKIP QUESTION 2** - You MUST ask about the debt amount
                2. Ask all three questions IN ORDER
                3. Wait for each answer before proceeding
                4. Only transfer after collecting all three answers
                5. Transfer immediately after the closing statement
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

    @function_tool()
    async def collect_qualification_info(self):
        """
        Collects all three required pieces of information from the customer:
        total debt amount, number of credit cards, and employment status.
        """
        self.has_all_info = True  # Mark that we've attempted to collect the info

        return """Ask these three questions together: "Great! To see how much we can save you, I need to verify three things: Your rough total credit card debt? How many credit cards with balances? And are you employed, self-employed, or retired?" Then wait for their response and continue the conversation."""

    # ========================================================================================================
    # ========================================================================================================

    @function_tool()
    async def handle_excluded_loans(self):
        """
        Responds when customer mentions secured loans or other debt types
        not covered by the program (HELOC, Mortgage, Auto Loans, Payday loans,
        Medical bills, Utility bills, Home Improvement Loans, Solar Loans).
        """

        prompt = """
        You should explain that you specifically work with unsecured 
        debt like credit cards. For secured loans like mortgages or 
        auto loans, inform them they'd need to work directly with 
        those lenders.
        """
        return prompt

    @function_tool()
    async def handle_no_debt(self):
        """
        Responds when customer claims they have no debt. Confirms whether
        they truly have no qualifying unsecured debt over $7,000.
        """

        prompt = """
        You should acknowledge this positively and then confirm by 
        asking if they have any unsecured debt like credit cards, 
        medical bills, or personal loans over $7,000.
        """
        return prompt

    @function_tool()
    async def handle_info_source_question(self):
        """
        Explains how the company obtained the customer's contact information
        when they ask about the source of their data.
        """

        prompt = """
        You should explain that their information likely came through 
        a financial inquiry they made online, such as a debt help form, 
        loan search, or credit evaluation. Emphasize that you only reach 
        out to people who've shown interest in financial relief options 
        and don't cold call randomly.
        """

        return prompt

    @function_tool()
    async def handle_angry_suspicious(self):
        """
        De-escalates situations where customers are angry or suspicious
        about the call's legitimacy.
        """

        prompt = """
        You should acknowledge their concern and explain that you don't 
        cold-call. Mention the information came through a financial lead 
        partner where someone expressed interest in debt relief options. 
        Offer to mark their file as not interested and remove it 
        immediately if they prefer.
        """
        return prompt

    @function_tool()
    async def handle_not_interested(self):
        """
        Attempts to re-engage customers who initially express no interest
        by understanding their specific situation.
        """

        prompt = """
        You should acknowledge their response and ask if it's because 
        they've already resolved their debts or just aren't sure what 
        this is about yet. Mention that many people say the same thing 
        until they hear how much they might save, and that it only 
        takes a couple minutes with no pressure.
        """
        return prompt

    @function_tool()
    async def handle_repeated_calls(self):
        """
        Addresses customer complaints about receiving multiple calls
        from the company.
        """

        prompt = """
        You should apologize for any excessive calling and explain it's 
        not intentional. Explain you reach out to individuals who showed 
        potential eligibility for debt relief, and the system may retry 
        if you haven't connected. Offer to mark them as not interested 
        but first ask if they'd like to quickly hear if they qualify, 
        as it could save them thousands.
        """
        return prompt

    @function_tool()
    async def handle_scam_concern(self):
        """
        Addresses customer concerns that this might be a scam by
        establishing credibility and legitimacy.
        """

        prompt = """
        You should acknowledge that phone scams are common these days. 
        Explain you're a licensed service provider and this isn't a 
        sales pitch. Clarify your goal is to walk through legitimate 
        debt reduction options available under federal and state programs, 
        and that they aren't agreeing to anything today - just getting 
        information they deserve to know.
        """
        return prompt

    @function_tool()
    async def handle_existing_program(self):
        """
        Responds when customer mentions they're already enrolled in
        another debt relief program.
        """

        prompt = """
        You should acknowledge positively that they're already taking 
        action. Ask how long they've been working with the other program. 
        Mention that sometimes people compare their current program with 
        yours and find they can actually reduce monthly payments or 
        shorten the term.
        """
        return prompt

    @function_tool()
    async def handle_how_it_works(self):
        """
        Explains the basic mechanics of how the debt relief program
        functions when customers ask for clarification.
        """

        prompt = """
        You should explain that based on their current debt and income, 
        you connect them to a program that helps lower the overall amount 
        they're responsible for and rolls everything into one manageable 
        monthly plan. Emphasize there are no loans and no credit pulls - 
        just a smarter way to get back in control.
        """

        return prompt

    # ========================================================================================================
    # ========================================================================================================
    @function_tool()
    async def handle_wrong_number(self):
        """
        Responds when the person claims it's a wrong number or they're not
        the right party. Attempts to salvage the call if they have debt.
        """

        prompt = """
        You should politely acknowledge the confusion and briefly mention 
        that you provide free advice on lowering credit card interest rates 
        and balances. If they owe money on credit cards, offer to help them 
        instead. Keep it brief and non-pushy.
        """
        return prompt

    @function_tool()
    async def handle_none_of_your_business(self):
        """
        Responds professionally when customer says their finances are
        none of the company's business.
        """

        prompt = """
        You should remain professional and briefly explain you're offering 
        free advice on reducing debt and eliminating future interest rates. 
        Emphasize it's a no-obligation call and respect their privacy.
        """
        return prompt

    @function_tool()
    async def handle_company_info_request(self):
        """
        Provides company address or phone number when customer requests
        verification of legitimacy.
        """

        prompt = """
        You should explain that you're based in Boca Raton, Florida, and 
        licensed in 49 states. Offer to connect them with a debt counselor 
        for more specific details if they need additional verification.
        """
        return prompt

    @function_tool()
    async def handle_program_details(self):
        """
        Explains how the debt relief program works when customer wants
        more detailed information.
        """

        prompt = """
        You should explain that you use debt mediation techniques with 
        pre-negotiated rates to reduce their debts, working with any 
        creditor. Keep the explanation simple but comprehensive.
        """
        return prompt

    @function_tool()
    async def handle_written_info_request(self):
        """
        Responds when customer wants everything in writing before
        proceeding with verbal discussion.
        """

        prompt = """
        You should explain that once prequalified, they'll receive 
        tailored information to review. Mention you're available for 
        any real-time questions they might have about the process.
        """
        return prompt

    @function_tool()
    async def handle_do_not_call(self):
        """
        Apologizes and takes action when customer mentions they're
        on the do-not-call list.
        """

        prompt = """
        You should immediately apologize for calling and assure them 
        you'll add them to your company's do-not-call list right away. 
        Be brief and respectful.
        """
        return prompt

    @function_tool()
    async def handle_card_closure_question(self):
        """
        Explains the credit card closure policy when customer asks
        if they need to close all their cards.
        """

        prompt = """
        You should explain they can choose which cards to keep or close. 
        Mention that your goal is to reduce their debt, and closing most 
        cards will help them get out of debt faster, but it's ultimately 
        their choice.
        """
        return prompt

    @function_tool()
    async def handle_savings_question(self):
        """
        Explains how the company achieves 40% savings when customer
        questions the specific percentage claims.
        """

        prompt = """
        You should explain that you negotiate with creditors to reduce 
        debts based on your relationships, pre-negotiated rates, and 
        industry trends. Be confident but not overly specific about 
        proprietary methods.
        """
        return prompt

    @function_tool()
    async def handle_tax_consequences(self):
        """
        Addresses customer concerns about tax implications of
        debt forgiveness.
        """

        prompt = """
        You should explain that credit card companies usually don't 
        report forgiven debt to the IRS, but recommend they consult 
        a CPA if they receive a 1099 form. Be clear you're not 
        providing tax advice.
        """
        return prompt

    # ========================================================================================================
    # ========================================================================================================
    @function_tool()
    async def handle_cannot_afford(self):
        """
        Responds when customer says they can't afford anything right now,
        addressing their financial concerns about taking on new obligations.
        """

        prompt = """
        You should empathetically acknowledge their struggle and explain 
        that's exactly why you're calling. Emphasize this program is 
        designed to reduce their overall monthly obligation, not add to it. 
        Clarify it's not a loan but a way to regain control of their finances.
        """
        return prompt

    @function_tool()
    async def handle_whats_the_catch(self):
        """
        Addresses customer skepticism when they ask what the catch is,
        building trust by explaining the straightforward nature of the program.
        """

        prompt = """
        You should reassure them there's no catch - just an option for 
        individuals in hardship to lower what they owe and make it manageable 
        again. Explain you're simply checking to see if they qualify, with 
        no hidden agenda.
        """
        return prompt

    @function_tool()
    async def handle_diy_objection(self):
        """
        Responds when customer claims they can handle debt relief themselves
        without paying for services.
        """

        prompt = """
        You should acknowledge that some people do try on their own, but 
        explain they usually don't get the same results. Emphasize that 
        your team works with creditors every day and knows how to make 
        these programs work in their favor.
        """
        return prompt

    @function_tool()
    async def handle_call_me_later(self):
        """
        Manages situations where customer wants to postpone the conversation,
        attempting to keep them engaged if they have qualifying debt.
        """

        prompt = """
        You should politely agree to call back but first ask if they're 
        dealing with unsecured debt right now or have already taken care 
        of it. If they confirm they have credit card debts, try to keep 
        them on the line by emphasizing it will only take a few minutes 
        and could save them thousands.
        """
        return prompt

    @function_tool()
    async def handle_already_handled(self):
        """
        Responds when customer claims they've already got their debt
        situation handled, exploring if there's still opportunity to help.
        """

        prompt = """
        You should respond positively and ask who they're working with. 
        Mention that sometimes people compare programs and realize they 
        can save more or shorten the term with your program. Don't be 
        pushy but plant the seed of potential better options.
        """
        return prompt

    @function_tool()
    async def handle_credit_impact(self):
        """
        Addresses customer concerns about how the program will affect
        their credit score.
        """

        prompt = """
        You should be honest that their credit may be impacted, but 
        point out that most people you help already have high balances 
        affecting their score. Emphasize your goal is long-term 
        improvement, not a quick fix. Focus on the bigger picture of 
        becoming debt-free.
        """
        return prompt

    @function_tool()
    async def handle_is_this_a_loan(self):
        """
        Clarifies that the program is not a loan when customers
        express concern about taking on new debt.
        """

        prompt = """
        You should clearly state it's not a loan and there's no new 
        credit line. Explain you simply work with what they currently 
        owe and restructure it into something manageable. Emphasize 
        this is about reducing debt, not creating more.
        """
        return prompt

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
    background_audio = BackgroundAudioPlayer()
    await background_audio.start(room=ctx.room, agent_session=session)

    # Register event handler BEFORE starting session
    ctx.room.on(
        "participant_attributes_changed", on_participant_attributes_changed_handler
    )
    agent_instance = GalacticVoiceAgent(
        f"{result['first_name']} {result['last_name']}" if result else None,
        result["lead_id"] if result else None,
        background_audio,
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
            num_idle_processes=10,
            prewarm_fnc=prewarm_fnc,
        )
    )
