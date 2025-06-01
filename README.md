# OpenDeep - Outbound Caller with Twilio, OpenAI, and Deepgram

OpenDeep is a streamlined version of the outbound caller application that exclusively uses the combination of:
- **Twilio** for making phone calls
- **OpenAI Realtime API** for conversational AI
- **Deepgram TTS** for high-quality text-to-speech

This implementation provides more humanlike voice output by combining OpenAI's conversation intelligence with Deepgram's high-quality voice synthesis.

## Features

- Real-time bidirectional audio streaming with Twilio
- OpenAI's Realtime API for natural conversational abilities
- Deepgram's TTS API for high-quality voice output
- Configurable voice models and call parameters
- Automatic call recording and transcription
- Simple, streamlined codebase focused on one implementation

## Requirements

- Python 3.8+
- Twilio account (Account SID, Auth Token, phone number)
- OpenAI API key with access to the Realtime API
- Deepgram API key with access to the TTS API

## Setup

1. Clone or download this repository
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and fill in your API keys:
   ```bash
   cp .env.example .env
   ```
4. Edit your `.env` file with the required credentials:
   ```
   # Twilio credentials
   TWILIO_ACCOUNT_SID=your_account_sid
   TWILIO_AUTH_TOKEN=your_auth_token
   TWILIO_PHONE_NUMBER=your_phone_number
   
   # OpenAI API key
   OPENAI_API_KEY=your_openai_api_key
   
   # Deepgram API key
   DEEPGRAM_API_KEY=your_deepgram_api_key
   ```

## Configuration

Configure your call by editing a JSON file in the `config/call_configs/` directory:

```json
{
  "phone_number": "+1234567890",
  "prompt": "You are an AI assistant making a call to confirm an appointment. Be friendly and professional.",
  "deepgram_model": "aura-2-thalia-en",
  "voice": "alloy",
  "save_recording": true,
  "max_duration": 600
}
```

Available Deepgram voice models:
- `aura-2-thalia-en`: Thalia voice (female, American English)
- `aura-2-stella-en`: Stella voice (female, American English)
- `aura-2-athena-en`: Athena voice (female, American English)
- `aura-2-hera-en`: Hera voice (female, American English)
- `aura-2-orion-en`: Orion voice (male, American English)
- `aura-2-helios-en`: Helios voice (male, American English)
- `aura-2-zeus-en`: Zeus voice (male, American English)
- `aura-2-apollo-en`: Apollo voice (male, American English)

## Usage

### Local Testing (Not suitable for production)

For local testing, run:

```bash
python main.py --config config/call_configs/your_config.json --port 8080 --verbose
```

This will start a local server, but Twilio won't be able to connect to it directly.

### Production Use

For production use, you need a public URL (e.g., using ngrok):

```bash
python main.py --config config/call_configs/your_config.json --stream-url wss://your-public-url.ngrok.io
```

## How It Works

1. The application establishes a WebSocket connection with Twilio for bidirectional audio streaming.
2. When audio is received from Twilio, it is forwarded to OpenAI's Realtime API.
3. OpenAI processes the audio, understands the conversation, and generates text responses.
4. These text responses are accumulated and sent as complete sentences to Deepgram's TTS API.
5. Deepgram converts the text to speech and returns audio in mu-law 8kHz format.
6. This audio is streamed back to Twilio in real-time.

## Project Structure

```
OpenDeep/
├── config/
│   └── call_configs/
│       └── default.json
├── data/
│   ├── audio/
│   ├── calls/
│   ├── logs/
│   └── transcripts/
├── config.py
├── conversation.py
├── deepgram_tts_async.py
├── llm.py
├── logger.py
├── main.py
├── requirements.txt
├── storage.py
├── twilio_handler_realtime.py
└── twilio_handler_realtime_humanlike.py
```

## Troubleshooting

- **No audio received**: Check that your Twilio credentials are correct and that your phone number is verified.
- **Connection errors**: Ensure your OpenAI API key has access to the Realtime API and your Deepgram API key is valid.
- **Poor quality audio**: Try adjusting the voice model or check network connectivity.