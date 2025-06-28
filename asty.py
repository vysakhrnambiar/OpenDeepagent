# ===============================================================================
# LEGACY FILE - NOT CURRENTLY USED IN ACTIVE SYSTEM (v16.0+)
# 
# Status: PRESERVED for reference/potential future use
# Last Active: Early development phases (v1-v12)
# Replacement: audio_processing_service/openai_realtime_client.py + audio_socket_handler.py
# Safe to ignore: This file is not imported by main.py or active services
# 
# Historical Context: Original standalone implementation with direct OpenAI Realtime
#                    integration before modular service architecture was developed.
#                    Contains working audio processing logic that may be useful for
#                    reference when enhancing current system.
# ===============================================================================
import asyncio
import numpy as np
import uuid
import struct
import json
import base64
import websockets
from dotenv import load_dotenv
import os
import wave
import logging
import time
import io

# --- Configuration ---
LOGGING_LEVEL = logging.INFO
# LOGGING_LEVEL = logging.DEBUG

OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
OPENAI_REALTIME_MODEL = "gpt-4o-realtime-preview-2024-10-01"
OPENAI_CONNECT_RETRIES = 3
OPENAI_CONNECT_RETRY_DELAY_S = 2

AST_SAMPLE_RATE = 8000
OPENAI_SAMPLE_RATE = 24000
PCM_SAMPLE_WIDTH_BYTES = 2  # Bytes per sample for 16-bit PCM

CLIENT_VAD_RMS_THRESHOLD = 0 
OUTPUT_GAIN_FACTOR = 2.5 # Initial gain for AI audio. EXPERIMENT with this.

AUDIOSOCKET_HOST = "0.0.0.0"
AUDIOSOCKET_PORT = 1200
AUDIOSOCKET_READ_TIMEOUT_S = 5.0
SESSION_INACTIVITY_TIMEOUT_S = 180
STATS_LOGGING_INTERVAL_S = 5.0

TYPE_HANGUP = 0x00
TYPE_UUID = 0x01
TYPE_DTMF = 0x03
TYPE_AUDIO = 0x10
TYPE_ERROR = 0xff

# --- Setup Logging ---
logging.basicConfig(
    level=LOGGING_LEVEL,
    format="%(asctime)s - %(levelname)s - [%(threadName)s] - %(filename)s:%(lineno)d - %(message)s"
)
logger = logging.getLogger(__name__)

# --- Load Environment Variables ---
load_dotenv()
OPENAI_API_KEY = os.getenv(OPENAI_API_KEY_ENV)
if not OPENAI_API_KEY:
    logger.critical(f"{OPENAI_API_KEY_ENV} not found in environment variables. Exiting.")
    exit(1)

# --- Helper Classes ---
class SessionAudioStats:
    def __init__(self, session_id, logging_interval_sec, ast_sr, openai_sr):
        self.session_id = session_id if session_id else "UnknownSession"
        self.logging_interval_sec = logging_interval_sec
        self.ast_sr = ast_sr
        self.openai_sr = openai_sr
        self.last_log_time = time.time()
        self.input_chunks_processed_period = 0
        self.input_chunks_sent_period = 0
        self.input_duration_sent_ms_period = 0.0
        self.input_chunks_discarded_vad_period = 0
        self.input_duration_discarded_vad_ms_period = 0.0
        self.output_chunks_received_from_openai_period = 0
        self.output_duration_received_from_openai_ms_period = 0.0
        self.output_chunks_sent_to_asterisk_period = 0
        self.output_duration_sent_to_asterisk_ms_period = 0.0

    def _reset_period_stats(self):
        self.input_chunks_processed_period = 0
        self.input_chunks_sent_period = 0
        self.input_duration_sent_ms_period = 0.0
        self.input_chunks_discarded_vad_period = 0
        self.input_duration_discarded_vad_ms_period = 0.0
        self.output_chunks_received_from_openai_period = 0
        self.output_duration_received_from_openai_ms_period = 0.0
        self.output_chunks_sent_to_asterisk_period = 0
        self.output_duration_sent_to_asterisk_ms_period = 0.0

    def update_session_id(self, session_id):
        self.session_id = session_id

    def record_input_chunk(self, original_ast_samples_count, sent_to_openai: bool, resampled_openai_samples_count_if_sent=0):
        self.input_chunks_processed_period += 1
        if sent_to_openai:
            self.input_chunks_sent_period += 1
            self.input_duration_sent_ms_period += (resampled_openai_samples_count_if_sent / self.openai_sr) * 1000
        else:
            self.input_chunks_discarded_vad_period += 1
            self.input_duration_discarded_vad_ms_period += (original_ast_samples_count / self.ast_sr) * 1000

    def record_output_chunk_received_from_openai(self, openai_pcm16_samples_count):
        self.output_chunks_received_from_openai_period += 1
        self.output_duration_received_from_openai_ms_period += (openai_pcm16_samples_count / self.openai_sr) * 1000
    
    def record_output_chunk_sent_to_asterisk(self, asterisk_pcm16_samples_count): # Tracks PCM16 samples sent
        self.output_chunks_sent_to_asterisk_period +=1
        self.output_duration_sent_to_asterisk_ms_period += (asterisk_pcm16_samples_count / self.ast_sr) * 1000

    def log_summary_if_needed(self, force_log=False):
        now = time.time()
        if not force_log and (now - self.last_log_time < self.logging_interval_sec):
            return False 
        log_items = []
        current_interval_duration = now - self.last_log_time
        if self.input_chunks_processed_period > 0:
            log_items.append(
                f"Input(->OpenAI): Proc {self.input_chunks_processed_period}, "
                f"Sent {self.input_chunks_sent_period} ({self.input_duration_sent_ms_period:.0f}ms), "
                f"VAD-Disc {self.input_chunks_discarded_vad_period} ({self.input_duration_discarded_vad_ms_period:.0f}ms)."
            )
        if self.output_chunks_received_from_openai_period > 0 or self.output_chunks_sent_to_asterisk_period > 0 :
            log_items.append(
                f"Output(AI->Ast): Recv_AI {self.output_chunks_received_from_openai_period} ({self.output_duration_received_from_openai_ms_period:.0f}ms PCM), "
                f"Sent_Ast {self.output_chunks_sent_to_asterisk_period} ({self.output_duration_sent_to_asterisk_ms_period:.0f}ms PCM)."
            )
        if log_items:
            logger.info(f"[{self.session_id}] Stats (last ~{current_interval_duration:.1f}s): {' | '.join(log_items)}")
        elif force_log: 
            logger.info(f"[{self.session_id}] Stats (last ~{current_interval_duration:.1f}s): No new audio activity.")
        self._reset_period_stats()
        self.last_log_time = now
        return True

# --- Audio Utilities ---
def calculate_rms(audio_np_s16: np.ndarray) -> float:
    if audio_np_s16.size == 0: return 0.0
    audio_float64 = audio_np_s16.astype(np.float64)
    return np.sqrt(np.mean(audio_float64**2))

def resample_audio(audio_np: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    if src_sr == dst_sr or audio_np.size == 0: return audio_np.astype(np.int16) 
    num_samples_dst = int(audio_np.size * dst_sr / src_sr)
    if num_samples_dst == 0: return np.array([], dtype=np.int16)
    if audio_np.size == 1: return np.full(num_samples_dst, audio_np[0], dtype=np.int16)
    x_src_indices = np.arange(audio_np.size)
    x_dst_indices = np.linspace(0, audio_np.size - 1, num_samples_dst, endpoint=True) 
    resampled_audio = np.interp(x_dst_indices, x_src_indices, audio_np)
    return np.round(resampled_audio).astype(np.int16)

# --- OpenAI Connection ---
async def connect_to_openai() -> websockets.WebSocketClientProtocol | None:
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "OpenAI-Beta": "realtime=v1"}
    endpoint = f"wss://api.openai.com/v1/realtime?model={OPENAI_REALTIME_MODEL}"
    logger.info(f"Attempting to connect to OpenAI: {endpoint}")
    for attempt in range(OPENAI_CONNECT_RETRIES):
        try:
            openai_ws = await websockets.connect(endpoint, extra_headers=headers)
            session_config = {
                "type": "session.update",
                "session": {
                    "modalities": ["audio", "text"],
                    "instructions": "You are a helpful AI assistant. Respond to user audio input with natural speech. Keep your responses concise and clear.",
                    "voice": "alloy", 
                    "input_audio_format": "pcm16", "output_audio_format": "pcm16",
                    "turn_detection": { "type": "server_vad", "threshold": 0.3, "prefix_padding_ms": 100, "silence_duration_ms": 1500 }
                }
            }
            await openai_ws.send(json.dumps(session_config))
            logger.info("Sent session.update to OpenAI. Waiting for session confirmation...")
            try:
                response_raw = await asyncio.wait_for(openai_ws.recv(), timeout=10.0) 
                response = json.loads(response_raw)
                if response.get("type") in ["session.success", "session.created"]:
                    session_id_from_resp = response.get('session', {}).get('id', 'N/A')
                    logger.info(f"OpenAI session successfully started/acknowledged (type: {response.get('type')}, session_id: {session_id_from_resp})")
                    return openai_ws
                elif response.get("type") == "error":
                    logger.error(f"OpenAI returned an error after session.update: {response.get('error')}")
                    await openai_ws.close(); return None 
                else:
                    logger.warning(f"Unexpected response type from OpenAI after session.update: {response.get('type')}. Full: {response_raw[:200]}")
                    return openai_ws 
            except asyncio.TimeoutError: logger.error("Timeout waiting for OpenAI session confirmation."); await openai_ws.close(); return None
            except Exception as e_recv: logger.error(f"Error receiving OpenAI session confirmation: {e_recv}"); await openai_ws.close(); return None
        except websockets.exceptions.InvalidStatusCode as e:
            logger.error(f"OpenAI connection attempt {attempt + 1}/{OPENAI_CONNECT_RETRIES} failed (HTTP Status {e.status_code}): {e.body.decode() if e.body else 'No body'}")
            if e.status_code == 401: raise Exception("OpenAI Authentication Failed (401).") 
            elif e.status_code == 429: 
                wait_time = OPENAI_CONNECT_RETRY_DELAY_S * (2 ** attempt) 
                logger.warning(f"OpenAI Rate Limited (429). Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)
            elif attempt < OPENAI_CONNECT_RETRIES - 1: await asyncio.sleep(OPENAI_CONNECT_RETRY_DELAY_S * (attempt + 1))
            else: raise Exception(f"Failed to connect to OpenAI after {OPENAI_CONNECT_RETRIES} retries (HTTP {e.status_code}).")
        except Exception as e: 
            logger.error(f"OpenAI connection attempt {attempt + 1}/{OPENAI_CONNECT_RETRIES} failed: {e}")
            if attempt < OPENAI_CONNECT_RETRIES - 1: await asyncio.sleep(OPENAI_CONNECT_RETRY_DELAY_S * (attempt + 1))
            else: raise Exception(f"Failed to connect to OpenAI after {OPENAI_CONNECT_RETRIES} retries.")
    raise Exception("Failed to connect to OpenAI (exhausted attempts).")

# --- Main AudioSocket Handler ---
async def handle_audiosocket(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    peer_addr = writer.get_extra_info('peername')
    session_internal_id = str(uuid.uuid4()).split('-')[0] 
    logger.info(f"[{session_internal_id}] New AudioSocket connection from {peer_addr}")

    session_asterisk_uuid = None 
    session_caller_audio_buffer = [] 
    session_ai_audio_buffer = []
    session_lock = asyncio.Lock()  
    
    stats = SessionAudioStats(session_internal_id, STATS_LOGGING_INTERVAL_S, AST_SAMPLE_RATE, OPENAI_SAMPLE_RATE)

    playback_buffer = bytearray() 
    playback_buffer_lock = asyncio.Lock()
    TARGET_ASTERISK_CHUNK_SIZE_BYTES = 320 # 20ms of 8kHz, 16-bit PCM

    openai_ws: websockets.WebSocketClientProtocol | None = None
    openai_receive_task: asyncio.Task | None = None
    playback_to_asterisk_task: asyncio.Task | None = None # Must be defined before try block for finally
    stats_logging_task: asyncio.Task | None = None


    # --- NESTED ASYNC FUNCTION DEFINITIONS ---
    # These need to be defined before they are used in asyncio.create_task()

    async def send_audio_to_asterisk_from_buffer():
        nonlocal playback_buffer, writer, stats, playback_buffer_lock # Make sure all needed outer vars are accessible
        logger.info(f"[{stats.session_id}] Starting send_audio_to_asterisk_from_buffer task.")
        try:
            while True: # This loop will run until explicitly broken or task cancelled
                chunk_to_send = None
                async with playback_buffer_lock: 
                    if len(playback_buffer) >= TARGET_ASTERISK_CHUNK_SIZE_BYTES:
                        chunk_to_send = playback_buffer[:TARGET_ASTERISK_CHUNK_SIZE_BYTES]
                        del playback_buffer[:TARGET_ASTERISK_CHUNK_SIZE_BYTES]
                
                if chunk_to_send:
                    payload_len = len(chunk_to_send)
                    header = struct.pack("!BH", TYPE_AUDIO, payload_len)
                    try:
                        if writer and not writer.is_closing():
                            writer.write(header + chunk_to_send)
                            await writer.drain()
                            num_samples_sent = payload_len // PCM_SAMPLE_WIDTH_BYTES
                            stats.record_output_chunk_sent_to_asterisk(asterisk_pcm16_samples_count=num_samples_sent) 
                            logger.debug(f"[{stats.session_id}] Sent {payload_len} bytes of 8kHz s16le PCM (buffered) to Asterisk.")
                        else:
                            logger.warning(f"[{stats.session_id}] Asterisk writer closed, cannot send buffered audio. Stopping send task.")
                            break 
                    except ConnectionError as e_write:
                        logger.error(f"[{stats.session_id}] Connection error sending buffered audio to Asterisk: {e_write}. Stopping send task.")
                        break 
                    except Exception as e_write_generic:
                        logger.error(f"[{stats.session_id}] Generic error sending buffered audio to Asterisk: {e_write_generic}. Stopping send task.")
                        break 
                    await asyncio.sleep(0.015) 
                else:
                    await asyncio.sleep(0.010)
        except asyncio.CancelledError:
            logger.info(f"[{stats.session_id}] send_audio_to_asterisk_from_buffer task cancelled.")
        finally:
            logger.info(f"[{stats.session_id}] send_audio_to_asterisk_from_buffer task finished.")


    async def send_chunk_to_openai_if_active(original_audio_bytes_ast: bytes):
        nonlocal stats, openai_ws, session_caller_audio_buffer, session_lock
        audio_np_ast = np.frombuffer(original_audio_bytes_ast, dtype=np.int16)
        rms_energy = calculate_rms(audio_np_ast)
        send_this_chunk_to_openai = rms_energy > CLIENT_VAD_RMS_THRESHOLD if CLIENT_VAD_RMS_THRESHOLD > 0 else True
        resampled_audio_np_openai = resample_audio(audio_np_ast, AST_SAMPLE_RATE, OPENAI_SAMPLE_RATE)

        async with session_lock:
            if resampled_audio_np_openai.size > 0:
                 session_caller_audio_buffer.append(resampled_audio_np_openai.copy())

        if send_this_chunk_to_openai:
            if resampled_audio_np_openai.size > 0 and openai_ws and openai_ws.open:
                audio_b64 = base64.b64encode(resampled_audio_np_openai.tobytes()).decode()
                try:
                    await openai_ws.send(json.dumps({
                        "type": "input_audio_buffer.append", 
                        "audio": audio_b64
                    }))
                    stats.record_input_chunk(
                        original_ast_samples_count=audio_np_ast.size,
                        sent_to_openai=True,
                        resampled_openai_samples_count_if_sent=resampled_audio_np_openai.size
                    )
                except websockets.ConnectionClosed:
                    logger.error(f"[{stats.session_id}] OpenAI WebSocket closed during send_audio_chunk.")
                    raise 
                except Exception as e:
                    logger.error(f"[{stats.session_id}] Error sending audio to OpenAI: {e}")
            elif resampled_audio_np_openai.size == 0:
                stats.record_input_chunk(original_ast_samples_count=audio_np_ast.size, sent_to_openai=False) 
        else: 
            stats.record_input_chunk(original_ast_samples_count=audio_np_ast.size, sent_to_openai=False)

    async def listen_for_openai_responses():
        nonlocal stats, openai_ws, session_ai_audio_buffer, session_lock, playback_buffer, playback_buffer_lock
        logger.info(f"[{stats.session_id}] Starting listen_for_openai_responses task.")
        try:
            while openai_ws and openai_ws.open:
                response_json = await openai_ws.recv()
                data = json.loads(response_json)
                msg_type = data.get("type")

                if msg_type == "error":
                    logger.error(f"[{stats.session_id}] OpenAI API Error: {data.get('error', data)}")
                elif msg_type == "response.audio.delta":
                    audio_data_b64 = data.get("delta")
                    if audio_data_b64:
                        ai_audio_bytes_openai_sr_pcm16 = base64.b64decode(audio_data_b64)
                        ai_audio_np_openai_sr_pcm16 = np.frombuffer(ai_audio_bytes_openai_sr_pcm16, dtype=np.int16)
                        
                        if ai_audio_np_openai_sr_pcm16.size > 0:
                            async with session_lock:
                                session_ai_audio_buffer.append(ai_audio_np_openai_sr_pcm16.copy())
                            stats.record_output_chunk_received_from_openai(openai_pcm16_samples_count=ai_audio_np_openai_sr_pcm16.size)
                            
                            ai_audio_np_ast_sr_pcm16 = resample_audio(ai_audio_np_openai_sr_pcm16, OPENAI_SAMPLE_RATE, AST_SAMPLE_RATE)
                            if ai_audio_np_ast_sr_pcm16.size > 0:
                                temp_audio_float = ai_audio_np_ast_sr_pcm16.astype(np.float32) * OUTPUT_GAIN_FACTOR
                                temp_audio_float = np.clip(temp_audio_float, -32768.0, 32767.0)
                                audio_to_send_pcm16_bytes = temp_audio_float.astype(np.int16).tobytes()
                                
                                async with playback_buffer_lock:
                                    playback_buffer.extend(audio_to_send_pcm16_bytes)
                                logger.debug(f"[{stats.session_id}] Added {len(audio_to_send_pcm16_bytes)} bytes to playback buffer (gain: {OUTPUT_GAIN_FACTOR}, total: {len(playback_buffer)}).")
                
                elif msg_type == "response.audio_transcript.delta":
                    delta_content = data.get('delta')
                    transcript_text = "" # Default to empty string
                    if isinstance(delta_content, dict):
                        transcript_text = delta_content.get('text', '')
                    elif isinstance(delta_content, str):
                        transcript_text = delta_content
                    if transcript_text: 
                        logger.info(f"[{stats.session_id}] OpenAI Tx: \"{transcript_text}\"")

                elif msg_type == "response.audio_transcript.done":
                    transcript_content = data.get('transcript')
                    full_transcript = "[No full transcript text provided]" # Default
                    if isinstance(transcript_content, dict):
                        full_transcript = transcript_content.get('text', '[No full transcript text in dict]')
                    elif isinstance(transcript_content, str):
                        full_transcript = transcript_content
                    logger.info(f"[{stats.session_id}] OpenAI Tx FINAL: \"{full_transcript}\"")
                
                elif msg_type == "response.done":
                    logger.info(f"[{stats.session_id}] OpenAI Event: response.done (AI turn finished).")

                elif msg_type in ["input_audio_buffer.speech_started", "input_audio_buffer.speech_stopped", 
                                  "session.updated", "session.created", "response.created", "response.audio.done"]:
                    logger.debug(f"[{stats.session_id}] OpenAI Event: Type='{msg_type}', Snippet='{response_json[:120]}...'")
                
                elif msg_type in ["input_audio_buffer.committed", "conversation.item.created", 
                                  "response.output_item.added", "response.content_part.added", 
                                  "response.content_part.done", "response.output_item.done", 
                                  "rate_limits.updated"]:
                    logger.debug(f"[{stats.session_id}] OpenAI Informational Event: Type='{msg_type}'")
                else: 
                    logger.info(f"[{stats.session_id}] OpenAI (Unknown/Unexpected Msg Type '{msg_type}'): {response_json[:200]}...")
        except websockets.ConnectionClosed as e:
            if e.code in [1000, 1001]: 
                 logger.info(f"[{stats.session_id}] OpenAI WebSocket closed gracefully (code: {e.code}, reason: '{e.reason}').")
            else: 
                 logger.warning(f"[{stats.session_id}] OpenAI WebSocket closed unexpectedly (code: {e.code}, reason: '{e.reason}').")
        except Exception as e:
            logger.error(f"[{stats.session_id}] OpenAI listen_for_openai_responses loop error: {e}", exc_info=True)
        finally:
            logger.info(f"[{stats.session_id}] OpenAI listen_for_openai_responses loop finished.")

    async def run_periodic_stats_logger(current_stats_obj: SessionAudioStats):
        logger.info(f"[{current_stats_obj.session_id}] Starting run_periodic_stats_logger task.")
        try:
            while True:
                await asyncio.sleep(current_stats_obj.logging_interval_sec)
                current_stats_obj.log_summary_if_needed()
        except asyncio.CancelledError:
            logger.info(f"[{current_stats_obj.session_id}] Periodic stats logger task cancelled.")
        finally:
            logger.info(f"[{current_stats_obj.session_id}] run_periodic_stats_logger task finished.")
    
    # --- Main session handling logic for AudioSocket ---
    try:
        openai_ws = await connect_to_openai()
        if not openai_ws: 
            logger.error(f"[{stats.session_id}] Failed to establish OpenAI session. Closing AudioSocket from {peer_addr}.")
            return 

        # Create tasks for concurrent operations AFTER their definitions
        openai_receive_task = asyncio.create_task(listen_for_openai_responses())
        playback_to_asterisk_task = asyncio.create_task(send_audio_to_asterisk_from_buffer())
        stats_logging_task = asyncio.create_task(run_periodic_stats_logger(stats))
        
        last_activity_time = time.time() 
        while True: 
            if time.time() - last_activity_time > SESSION_INACTIVITY_TIMEOUT_S:
                logger.warning(f"[{stats.session_id}] Session timed out due to {SESSION_INACTIVITY_TIMEOUT_S}s inactivity from Asterisk.")
                break
            
            # Check if critical tasks have ended
            if openai_receive_task and openai_receive_task.done():
                logger.warning(f"[{stats.session_id}] OpenAI receive task ended. Checking for errors.")
                try: openai_receive_task.result() 
                except asyncio.CancelledError: logger.info(f"[{stats.session_id}] OpenAI receive task was cancelled.")
                except Exception as e_task: logger.error(f"[{stats.session_id}] OpenAI receive task failed: {e_task}. Closing session.")
                else: logger.info(f"[{stats.session_id}] OpenAI receive task finished without error (e.g. OpenAI closed connection).")
                break 
            if playback_to_asterisk_task and playback_to_asterisk_task.done(): # Also check playback task
                logger.warning(f"[{stats.session_id}] Playback to Asterisk task ended. Checking for errors.")
                try: playback_to_asterisk_task.result()
                except asyncio.CancelledError: logger.info(f"[{stats.session_id}] Playback to Asterisk task was cancelled.")
                except Exception as e_task_pb: logger.error(f"[{stats.session_id}] Playback to Asterisk task failed: {e_task_pb}. Closing session.")
                else: logger.info(f"[{stats.session_id}] Playback to Asterisk task finished without error.")
                break
            
            try: 
                header = await asyncio.wait_for(reader.readexactly(3), timeout=AUDIOSOCKET_READ_TIMEOUT_S)
                last_activity_time = time.time() 
            except asyncio.TimeoutError:
                logger.debug(f"[{stats.session_id}] No data from AudioSocket for {AUDIOSOCKET_READ_TIMEOUT_S}s. Continuing to listen...")
                stats.log_summary_if_needed() 
                continue 
            except (asyncio.IncompleteReadError, ConnectionError, OSError) as e: 
                logger.warning(f"[{stats.session_id}] AudioSocket connection closed/error (header read): {e}")
                break
            if not header: 
                logger.warning(f"[{stats.session_id}] AudioSocket connection closed (empty header received).")
                break

            msg_type = header[0]
            payload_len = struct.unpack("!H", header[1:3])[0]
            payload = b''

            if payload_len > 0:
                try:
                    payload_read_timeout = max(1.0, (payload_len / AST_SAMPLE_RATE / PCM_SAMPLE_WIDTH_BYTES) * 2) 
                    payload = await asyncio.wait_for(reader.readexactly(payload_len), timeout=payload_read_timeout) 
                    last_activity_time = time.time()
                except asyncio.TimeoutError:
                    logger.warning(f"[{stats.session_id}] Timeout reading payload from AudioSocket (expected {payload_len} bytes).")
                    continue
                except (asyncio.IncompleteReadError, ConnectionError, OSError) as e:
                    logger.warning(f"[{stats.session_id}] AudioSocket connection closed/error (payload read): {e}")
                    break
            
            if msg_type == TYPE_HANGUP:
                logger.info(f"[{stats.session_id}] Received HANGUP from Asterisk.")
                break
            elif msg_type == TYPE_UUID:
                session_asterisk_uuid = str(uuid.UUID(bytes=payload))
                stats.update_session_id(session_asterisk_uuid) 
                logger.info(f"[{stats.session_id}] Received Asterisk Call UUID: {session_asterisk_uuid}")
            elif msg_type == TYPE_AUDIO:
                if openai_ws and openai_ws.open:
                    await send_chunk_to_openai_if_active(payload)
                else: 
                    logger.warning(f"[{stats.session_id}] Received audio from Asterisk, but OpenAI WS not open/initialized. Discarding audio.")
                    stats.record_input_chunk(original_ast_samples_count=len(payload)//PCM_SAMPLE_WIDTH_BYTES, sent_to_openai=False) 
            elif msg_type == TYPE_DTMF:
                dtmf_digit = payload.decode('ascii', errors='ignore') if payload else '?'
                logger.info(f"[{stats.session_id}] Received DTMF: {dtmf_digit}")
            elif msg_type == TYPE_ERROR:
                logger.error(f"[{stats.session_id}] Received ERROR from AudioSocket: {payload.decode('utf-8', errors='ignore')}")
            else:
                logger.warning(f"[{stats.session_id}] Unknown message type {msg_type} from AudioSocket (payload len: {payload_len}).")
            stats.log_summary_if_needed() 
    except websockets.exceptions.ConnectionClosed as e_ws_main: 
        logger.error(f"[{stats.session_id}] Main loop: OpenAI WebSocket connection closed: {e_ws_main.reason} (code: {e_ws_main.code})")
    except ConnectionError as e_conn_main: 
        logger.warning(f"[{stats.session_id}] Main loop: AudioSocket Connection error: {e_conn_main}")
    except Exception as e_main_loop:
        logger.error(f"[{stats.session_id}] Unhandled error in handle_audiosocket main loop: {e_main_loop}", exc_info=True)
    finally:
        final_session_id_log = stats.session_id 
        logger.info(f"[{final_session_id_log}] Cleaning up AudioSocket session for {peer_addr} (Asterisk UUID: {session_asterisk_uuid or 'N/A'}).")
        stats.log_summary_if_needed(force_log=True) 

        tasks_to_cancel = [stats_logging_task, openai_receive_task, playback_to_asterisk_task]
        for task in tasks_to_cancel:
            if task and not task.done():
                task.cancel()
                try: await task
                except asyncio.CancelledError: logger.info(f"[{final_session_id_log}] Task {task.get_name()} successfully cancelled.") # Python 3.8+ for get_name()
                except Exception as e_task_cancel: logger.error(f"[{final_session_id_log}] Error awaiting cancelled task: {e_task_cancel}")
        
        if openai_ws and openai_ws.open:
            try:
                logger.info(f"[{final_session_id_log}] Closing OpenAI WebSocket connection.")
                await openai_ws.close(code=1000, reason="Client session ending normally")
            except Exception as e_ws_close:
                logger.error(f"[{final_session_id_log}] Error closing OpenAI WebSocket: {e_ws_close}")

        wav_id_for_file = final_session_id_log if final_session_id_log != "UnknownSession" else session_internal_id
        final_caller_audio, final_ai_audio = np.array([], dtype=np.int16), np.array([], dtype=np.int16)
        async with session_lock: 
            if session_caller_audio_buffer:
                final_caller_audio = np.concatenate(session_caller_audio_buffer)
            if session_ai_audio_buffer:
                final_ai_audio = np.concatenate(session_ai_audio_buffer)
        
        if final_caller_audio.size == 0 and final_ai_audio.size == 0:
            logger.info(f"[{wav_id_for_file}] No audio data recorded for this session. Skipping WAV file generation.")
        else:
            maxlen = max(final_caller_audio.size, final_ai_audio.size)
            if final_caller_audio.size < maxlen:
                final_caller_audio = np.pad(final_caller_audio, (0, maxlen - final_caller_audio.size), 'constant', constant_values=0)
            if final_ai_audio.size < maxlen:
                final_ai_audio = np.pad(final_ai_audio, (0, maxlen - final_ai_audio.size), 'constant', constant_values=0)
            
            stereo_audio = np.stack((final_caller_audio, final_ai_audio), axis=-1).astype(np.int16)
            wav_filename = f"call_recording_{wav_id_for_file}.wav"
            try:
                with wave.open(wav_filename, 'wb') as wf:
                    wf.setnchannels(2); wf.setsampwidth(PCM_SAMPLE_WIDTH_BYTES); wf.setframerate(OPENAI_SAMPLE_RATE)
                    wf.writeframes(stereo_audio.tobytes())
                logger.info(f"[{wav_id_for_file}] Saved stereo call recording to {wav_filename}")
            except Exception as e_wav:
                logger.error(f"[{wav_id_for_file}] Failed to save WAV file {wav_filename}: {e_wav}")
        
        if writer and not writer.is_closing():
            writer.close()
            try: await writer.wait_closed()
            except Exception as e_writer: logger.error(f"[{final_session_id_log}] Error during writer.wait_closed(): {e_writer}")
        logger.info(f"[{final_session_id_log}] AudioSocket connection from {peer_addr} fully closed.")

# --- Main Server Execution ---
async def main():
    server = await asyncio.start_server(handle_audiosocket, AUDIOSOCKET_HOST, AUDIOSOCKET_PORT)
    logger.info(f"TCP AudioSocket server running on {AUDIOSOCKET_HOST}:{AUDIOSOCKET_PORT}")
    logger.info(f"OpenAI Model: {OPENAI_REALTIME_MODEL}, Output Gain for AI: {OUTPUT_GAIN_FACTOR}")
    logger.info(f"Client-side VAD RMS Threshold: {CLIENT_VAD_RMS_THRESHOLD} (0 means VAD disabled)")
    logger.info(f"Audio stats will be logged approx. every {STATS_LOGGING_INTERVAL_S} seconds per session.")
    
    async with server: 
        await server.serve_forever()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Server shutting down due to KeyboardInterrupt...")
    except Exception as e_main_exec: 
        logger.critical(f"Server encountered a critical error in main execution: {e_main_exec}", exc_info=True)
    finally:
        logger.info("Server shutdown process complete.")