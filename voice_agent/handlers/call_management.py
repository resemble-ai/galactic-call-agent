from livekit.agents import function_tool


class CallManagementHandlers:
    # Remove end_call and detected_answering_machine from here
    # since they need special handling in the main agent

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
