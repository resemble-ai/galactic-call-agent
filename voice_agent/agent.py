import logging
from dotenv import load_dotenv
import os
from livekit import agents, api, rtc
from livekit.agents import (
    AgentSession,
    Agent,
    RoomInputOptions,
    RunContext,
    function_tool,
    get_job_context,
)
from livekit.plugins import (
    openai,
    cartesia,
    deepgram,
    noise_cancellation,
    silero,
    resemble,
    elevenlabs,
)
from livekit.plugins.turn_detector.multilingual import MultilingualModel

load_dotenv(dotenv_path=".env.local")
print(os.getenv("RESEMBLE_API_KEY"))

logger = logging.getLogger("outbound-caller")
logger.setLevel(logging.DEBUG)


class GalacticVoiceAgent(Agent):
    instruction = """
    You are a professional debt relief specialist calling from Galactic Consumer Service. Your goal is to help qualified customers reduce their credit card debt through legitimate debt relief programs.

    ## Initial Greeting
    Start the conversation warmly and professionally:
    "Good [Morning/Afternoon/Evening], Mr./Ms. [Last Name]. My name is [Your Name] and I'm calling from Galactic Consumer Service. I'm reaching out to you today because per our records it looks like you still have more than seven thousand dollars in credit card debt and you've been making your monthly payments on time. Is that right?"

    ## These questions are mandatory to ask, make sure in no circumstances these are missed
    After the customer responds, you need to gather three essential pieces of information:

    1. **Total Debt Amount** - Use function: get_total_debt_amount
    - Ask about their total credit card debt in a conversational manner
    - This determines if they meet the $7,000 minimum threshold

    2. **Number of Credit Cards** - Use function: get_credit_card_count  
    - Ask for a rough estimate of how many cards have balances
    - This helps assess the complexity of their situation

    3. **Employment Status** - Use function: get_employment_status
    - Determine if they're employed, self-employed, or retired
    - This confirms their ability to make monthly payments

    ## Edge Scenarios - Use Appropriate Functions:

    ### Initial Resistance/Objections:
    - **"I don't have any debt"** → Use: handle_no_debt
    - **"How did you get my information?"** → Use: handle_info_source_question
    - **"Is this a scam?"** → Use: handle_scam_concern
    - **"I'm not interested"** → Use: handle_not_interested
    - **"Stop calling me repeatedly"** → Use: handle_repeated_calls
    - **Angry or suspicious behavior** → Use: handle_angry_suspicious
    - **"Wrong number/Not me"** → Use: handle_wrong_number
    - **"None of your business"** → Use: handle_none_of_your_business
    - **"I'm on the do-not-call list"** → Use: handle_do_not_call

    ### Verification/Trust Building:
    - **"What's your company address/phone?"** → Use: handle_company_info_request
    - **"I want everything in writing"** → Use: handle_written_info_request

    ### Program-Specific Questions:
    - **"How does this work?"** → Use: handle_how_it_works
    - **"How does the program work?"** → Use: handle_program_details
    - **"I'm already in a program"** → Use: handle_existing_program
    - **"Do I need to close all my cards?"** → Use: handle_card_closure_question
    - **"How do you save 40%?"** → Use: handle_savings_question
    - **"What about taxes?"** → Use: handle_tax_consequences
    - **Mentions excluded loan types** → Use: handle_excluded_loans
    
    ### Financial Concerns:
    - **"I can't afford anything"** → Use: handle_cannot_afford
    - **"What's the catch?"** → Use: handle_whats_the_catch
    - **"I can do this myself"** → Use: handle_diy_objection
    - **"Is this a loan?"** → Use: handle_is_this_a_loan

    ### Timing/Delay Tactics:
    - **"Call me later"** → Use: handle_call_me_later
    - **"I've already handled this"** → Use: handle_already_handled

    ### Program Impact Questions:
    - **"Will this hurt my credit?"** → Use: handle_credit_impact
    
    ## Important Behavioral Guidelines:

    ### When to End the Call Immediately:
    - Customer says they're on the do-not-call list (apologize and end)
    - Customer becomes verbally abusive or threatening
    - Customer explicitly asks to be removed after you've attempted one rebuttal

    ### When to Pivot:
    - If it's wrong number but they have debt, offer to help them instead
    - If they're suspicious, focus on building trust before qualifying
    - If they want written info, offer to qualify them first for relevant materials

    ### Conversation Flow:
    - Always acknowledge their concern before responding
    - Use transitional phrases like "I understand" or "That's a fair question"
    - Keep responses concise but complete
    - Don't repeat the same rebuttal if they've already rejected it

    ## Qualification Summary
    After gathering all three pieces of information, provide a brief summary:
    "OK, alright, thanks for your answers. Based on what you've shared - [summarize their debt amount, number of cards, and employment status] - we have multiple options where your savings can be significant, and your monthly payments can be considerably lower."

    ## Call Conclusion Scenarios:
    ### If Qualified and Interested:
    "Great! The next step is to connect you with one of our debt specialists who can review your specific situation and show you exactly how much you could save. They'll go over all your options with no obligation. Would you prefer to speak with them now, or should we schedule a time that works better for you?"

    ## Important Reminders:
    - Always maintain a helpful, consultative tone rather than pushy sales approach
    - If unsure about a response, err on the side of being helpful and transparent
    - Never make promises about specific savings without proper qualification
    - Respect their time and decision if they're not interested
    - End every call professionally, regardless of outcome
    """

    def __init__(self) -> None:
        super().__init__(instructions=self.instruction)

    @function_tool()
    async def get_total_debt_amount(self):
        """
        Asks the customer to provide their total credit card debt amount.
        This helps qualify them for the debt relief program by determining
        if they meet the minimum $7,000 threshold.
        """

        prompt = """
        You should ask the customer about their total credit card debt 
        in a conversational way. Request a ballpark figure using examples 
        like $10,000, $20,000, $25,000 or more. Emphasize this is just 
        a rough estimate off the top of their head.
        """
        return prompt

    @function_tool()
    async def get_credit_card_count(self):
        """
        Asks the customer how many credit cards they have with outstanding balances.
        This information helps determine the complexity of their debt situation
        and the appropriate consolidation approach.
        """

        prompt = """
        You should ask the customer for a rough estimate of how many 
        credit cards they owe balances on. Suggest ranges like two, 
        three, four, or more to make it easy for them to provide a 
        quick estimate.
        """
        return prompt

    @function_tool()
    async def get_employment_status(self):
        """
        Determines the customer's current employment situation to assess
        their ability to make monthly payments and qualify for specific
        debt relief programs.
        """

        prompt = """
        You should ask if the customer is currently employed, 
        self-employed, or retired. Ask this in a straightforward 
        manner to quickly categorize their income situation.
        """
        return prompt

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


async def entrypoint(ctx: agents.JobContext):
    session = AgentSession(
        stt=deepgram.STT(model="nova-3", language="multi"),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=elevenlabs.TTS(
            api_key="sk_e09e83bf20fd499d5b983625b670e9bb6484ea3b4da70f1e",
            voice_id="NwhlWbOasPHy5FAy7b7U",
        ),
        vad=silero.VAD.load(),
        turn_detection=MultilingualModel(),
    )

    await session.start(
        room=ctx.room,
        agent=GalacticVoiceAgent(),
        room_input_options=RoomInputOptions(
            # LiveKit Cloud enhanced noise cancellation
            # - If self-hosting, omit this parameter
            # - For telephony applications, use `BVCTelephony` for best results
            noise_cancellation=noise_cancellation.BVC(),
        ),
    )

    await ctx.connect()

    await session.generate_reply()


if __name__ == "__main__":
    agents.cli.run_app(
        agents.WorkerOptions(
            entrypoint_fnc=entrypoint, agent_name="incoming-call-agent"
        )
    )
