import sys
from pathlib import Path
import openai
from openai import AsyncOpenAI
from fastapi import UploadFile
import tempfile
import os

# --- Path Hack ---
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
# --- End Path Hack ---

from config.app_config import app_config
from common.logger_setup import setup_logger

logger = setup_logger(__name__)

class OpenAIAudioClient:
    def __init__(self):
        if not app_config.OPENAI_API_KEY:
            logger.error("OpenAI API key is not configured.")
            raise ValueError("OpenAI API key is missing.")
        
        self.async_client = AsyncOpenAI(api_key=app_config.OPENAI_API_KEY)
        self.model = "gpt-4o-transcribe"

    async def transcribe_audio(self, audio_file: UploadFile) -> str:
        """
        Transcribes an audio file using OpenAI's transcription service.
        """
        temp_audio_path = None
        try:
            # Use a temporary file to avoid filename conflicts and ensure cleanup
            with tempfile.NamedTemporaryFile(delete=False, suffix=".webm") as temp_audio_file:
                content = await audio_file.read()
                if not content:
                    logger.error("Received an empty audio file.")
                    return ""
                temp_audio_file.write(content)
                temp_audio_path = temp_audio_file.name

            # Now, open the temporary file for transcription
            with open(temp_audio_path, "rb") as audio_file_obj:
                transcription = await self.async_client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file_obj
                )
            
            logger.debug(f"Successfully transcribed audio file: {audio_file.filename}")
            return transcription.text
            
        except openai.APIError as e:
            logger.error(f"OpenAI API error during transcription: {e}", exc_info=True)
            return ""
        except Exception as e:
            logger.error(f"Unexpected error during transcription: {e}", exc_info=True)
            return ""
        finally:
            # Clean up the temporary file
            if temp_audio_path and os.path.exists(temp_audio_path):
                os.remove(temp_audio_path)