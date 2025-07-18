# Voice Agent for Galactic

A voice agent application built with LiveKit that handles inbound calls, processes lead information, and provides intelligent conversational AI capabilities.

## Overview

This repository contains a voice agent system designed to:

- Handle inbound calls through SIP integration
- Retrieve and update lead information dynamically
- Provide intelligent conversational AI responses using LLMs
- Track call metrics and dispositions
- Support multiple TTS providers (Resemble, Cartesia, ElevenLabs)

### Key Components

- **main.py** - Entry point containing the voice agent implementation with event handling and metrics collection
- **apis/** - Contains API modules for lead management:
  - `get_lead_info.py` - Retrieves caller information
  - `update_lead.py` - Updates call status and disposition
- **GalacticVoiceAgent/** - Core agent architecture and conversation logic
  - `agent.py` - Main agent implementation
  - `system_prompt.py` - System prompt configuration
- **status_codes.py** - Constants for call disposition codes
- **metrics_csv_logger.py** - Metrics logging functionality for development

## Getting Started

### Prerequisites

- Python 3.8+
- LiveKit server access
- API keys for chosen TTS provider (Resemble, Cartesia, or ElevenLabs)
- Additional API keys as required by your configuration

### Installation

1. **Create a virtual environment**

   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

2. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment variables**

   ```bash
   cp .env.example .env.local
   ```

   Update `.env.local` with your API keys and configuration settings

4. **Download required files**
   ```bash
   python main.py download-files
   ```

### Running the Application

**Development mode:**

```bash
python main.py dev
```

**Production mode:**

```bash
python main.py start
```

## Architecture

The application uses LiveKit's agent framework to:

- Connect to SIP participants
- Process speech-to-text using Deepgram
- Generate responses using LLMs (OpenAI/Cerebras)
- Convert text to speech using configurable TTS providers
- Track call metrics and handle various call dispositions
