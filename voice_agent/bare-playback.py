from dotenv import load_dotenv
import os

from livekit import agents
from livekit.agents import AgentSession, Agent, RoomInputOptions
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
from livekit.agents.utils.audio import audio_frames_from_file

load_dotenv(dotenv_path=".env.local")


class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(instructions="You are a helpful voice AI assistant.")



async def entrypoint(ctx: agents.JobContext):
    await ctx.connect()

    session = AgentSession(
        stt=deepgram.STT(model="nova-2-phonecall"),
        llm=google.LLM(model="gemini-2.5-flash-lite-preview-06-17"),
        tts=resemble.TTS(api_key=os.getenv("RESEMBLE_API_KEY"),voice_uuid="332aece2"),
        vad=silero.VAD.load(),
        turn_detection=MultilingualModel(),
    )

    await session.start(
        room=ctx.room,
        agent=Assistant(),
        room_input_options=RoomInputOptions(
            # LiveKit Cloud enhanced noise cancellation
            # - If self-hosting, omit this parameter
            # - For telephony applications, use `BVCTelephony` for best results
            noise_cancellation=noise_cancellation.BVCTelephony(), 
        ),
    )

    sound = audio_frames_from_file("welcome.wav")
    await session.say(
        text="Through a sunlit forest, a curious fox with a fluffy tail and bright, glimmering eyes darted between the trees, its tiny paws leaving soft imprints on the mossy ground. Pausing to sniff a patch of wildflowers, it tilted its head, listening to the distant chirp of a bird, as if the entire forest were a melody it alone could understand.",
        audio=sound,
    )

    await session.generate_reply(
        instructions="Greet the user and offer your assistance."
    )

if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint, agent_name="incoming-call-agent"))