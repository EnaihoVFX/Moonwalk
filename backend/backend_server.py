"""
Moonwalk — Backend Server (Agentic)
=====================================
WebSocket server that:
  1. Receives audio from Electron → detects wake word → transcribes speech
  2. Passes transcribed text to the Agent Loop (perception + planning + tools)
  3. Streams UI state updates back to the Electron overlay
"""

import asyncio
import websockets
import json
import base64
import wave
import io
import struct
import numpy as np
import sys
import os
from functools import partial

# Force print to flush immediately so Electron gets the logs in real-time
print = partial(print, flush=True)

# Voice libraries
import pvporcupine
import speech_recognition as sr

# Moonwalk Agent
from agent import MoonwalkAgent
import perception
from tools import set_sub_agent_manager
from cloud_server import SubAgentManager

# ==========================================
# IMPORTANT: PASTE YOUR PICOVOICE ACCESS KEY HERE
PICOVOICE_ACCESS_KEY = "lDvqq7J641WbqdzMsPCdLlawELhfGZOGhaceFzl3ZYYYzeeuXq55YA=="
# ==========================================


class VoiceAssistant:
    def __init__(self):
        self.state = "IDLE"  # IDLE, LISTENING, LOADING, DOING
        self.porcupine = None
        self.agent = MoonwalkAgent()  # The agentic brain
        
        # We try to initialize Porcupine, but it will fail if the key is default
        try:
            if PICOVOICE_ACCESS_KEY != "YOUR_PICOVOICE_ACCESS_KEY_HERE":
                base_dir = os.path.dirname(os.path.abspath(__file__))
                custom_ppn = os.path.join(base_dir, "hey_moonwalk.ppn")
                
                if os.path.exists(custom_ppn):
                    self.porcupine = pvporcupine.create(
                        access_key=PICOVOICE_ACCESS_KEY,
                        keyword_paths=[custom_ppn]
                    )
                    print(f"Porcupine initialized with CUSTOM wake word: 'Hey Moonwalk'")
                else:
                    self.porcupine = pvporcupine.create(
                        access_key=PICOVOICE_ACCESS_KEY,
                        keywords=["porcupine"]
                    )
                    print(f"Porcupine initialized with built-in keyword 'Porcupine'.")
                    print("NOTE: To use 'Hey Moonwalk', place 'hey_moonwalk.ppn' in the project root.")
            else:
                print("WARNING: Picovoice Access Key not set. Wake word detection will not work.")
        except Exception as e:
            print(f"Failed to initialize Porcupine: {e}")

        # For capturing the command after wake
        self.audio_buffer = bytearray()
        self.recognizer = sr.Recognizer()
        self.consecutive_silence_chunks = 0
        self.SILENCE_THRESHOLD_CHUNKS = 7  # Approx 0.45 seconds of sustained silence
        self.MIN_BUFFER_SIZE = 16000 * 2 * 0.3  # Min 0.3 seconds of audio
        self.grace_chunks_remaining = 0  # Grace period after await_reply
        self.waiting_for_voice = False  # True = require voice onset before recording
        self.waiting_for_reply = False  # True = do not abort on STT silence

    async def handle_audio_chunk(self, websocket, b64_payload):
        """Decode base64 WAV, strip header, get raw PCM bytes."""
        try:
            wav_bytes = base64.b64decode(b64_payload)
            
            # Use Python's wave module to read the WAV chunk
            with wave.open(io.BytesIO(wav_bytes), 'rb') as w:
                pcm_data = w.readframes(w.getnframes())
            
            if self.state == "IDLE":
                await self.process_wake_word(websocket, pcm_data)
            elif self.state == "LISTENING":
                await self.buffer_command(websocket, pcm_data)
                
        except Exception as e:
            print(f"Error processing audio chunk: {e}")

    async def process_wake_word(self, websocket, pcm_data):
        """Feed audio frames into Porcupine to detect wake word."""
        if not self.porcupine:
            return  # Can't detect without the engine

        chunk_size = 1024
        for i in range(0, len(pcm_data), chunk_size):
            chunk = pcm_data[i:i+chunk_size]
            if len(chunk) == chunk_size:
                pcm_tuple = struct.unpack_from("h" * self.porcupine.frame_length, chunk)
                
                keyword_index = self.porcupine.process(pcm_tuple)
                if keyword_index >= 0:
                    print("=> WAKE WORD DETECTED!")
                    self.state = "LISTENING"
                    self.audio_buffer = bytearray()
                    await websocket.send(json.dumps({
                        "type": "status", "state": "state-listening"
                    }))
                    break

    async def buffer_command(self, websocket, pcm_data):
        """Buffer incoming audio while listening, detect silence to stop."""

        # Grace period: discard audio (don't buffer silence before user speaks)
        if self.grace_chunks_remaining > 0:
            self.grace_chunks_remaining -= 1
            return

        # Calculate RMS of the incoming chunk
        ints = np.frombuffer(pcm_data, dtype=np.int16)
        if len(ints) == 0:
            return
        rms = np.sqrt(np.mean(ints.astype(np.float32)**2))

        # Phase 1: Wait for voice onset (after await_reply grace period)
        # Don't buffer until we hear actual speech — avoids capturing
        # silence or quiet system audio (e.g. YouTube playing)
        if self.waiting_for_voice:
            if rms > 1500:  # Voice onset threshold (significantly higher to filter out speaker audio bleed)
                print(f"[Audio] Voice detected (RMS={rms:.0f}), recording...")
                self.waiting_for_voice = False
                self.audio_buffer.extend(pcm_data)
            return  # Skip until voice detected

        # Phase 2: Normal buffering + silence detection
        self.audio_buffer.extend(pcm_data)

        if rms < 250:
            self.consecutive_silence_chunks += 1
        else:
            self.consecutive_silence_chunks = 0

        if len(self.audio_buffer) > self.MIN_BUFFER_SIZE:
            if self.consecutive_silence_chunks >= self.SILENCE_THRESHOLD_CHUNKS:
                print(f"=> SUSTAINED SILENCE ({self.consecutive_silence_chunks} chunks). Processing Command...")
                self.state = "LOADING"
                self.consecutive_silence_chunks = 0
                await websocket.send(json.dumps({
                    "type": "progress", "state": "state-loading"
                }))
                
                # Start processing the audio asynchronously
                asyncio.create_task(self.transcribe_and_act(websocket))

    async def transcribe_and_act(self, websocket):
        """
        Run Speech-to-Text on the buffered audio, then hand off
        to the agentic pipeline.
        """
        print(f"Transcribing {len(self.audio_buffer)} bytes of audio...")
        
        try:
            # Wrap raw PCM buffer into WAV for SpeechRecognition
            wav_io = io.BytesIO()
            with wave.open(wav_io, 'wb') as w:
                w.setnchannels(1)
                w.setsampwidth(2)  # 16-bit
                w.setframerate(16000)
                w.writeframes(self.audio_buffer)
            wav_io.seek(0)

            # Recognize using Google Web Speech API
            with sr.AudioFile(wav_io) as source:
                audio_data = self.recognizer.record(source)
                
            try:
                text = self.recognizer.recognize_google(audio_data)
                print(f"=> TRANSCRIBED: {text}")
                
                # ════════════════════════════════════════════
                #  AGENTIC PIPELINE — This is where the magic happens
                # ════════════════════════════════════════════
                
                # Define a callback to stream UI updates via WebSocket
                async def ws_callback(msg: dict):
                    try:
                        await websocket.send(json.dumps(msg))
                    except Exception as e:
                        print(f"[WS Callback] Error sending: {e}")

                # 1. Perception: Capture context snapshot
                # Run perception and router init concurrently
                context, _ = await asyncio.gather(
                    perception.snapshot(text),
                    self.agent.router.initialize(),
                )
                
                # 2. Agent Loop: Let the agent reason and act
                result = await self.agent.run(text, context, ws_callback=ws_callback)
                
                # Unpack: result is (response_text, awaiting_reply)
                if isinstance(result, tuple):
                    _, awaiting_reply = result
                else:
                    awaiting_reply = False
                
                # If await_reply was used, skip wake word for next input
                if awaiting_reply:
                    print("[Backend] Agent awaiting reply — listening without wake word")
                    self.state = "LISTENING"
                    self.audio_buffer = bytearray()
                    self.consecutive_silence_chunks = 0
                    self.grace_chunks_remaining = 64  # ~4s grace (stream + read time)
                    self.waiting_for_voice = True  # Require voice onset after grace
                    self.waiting_for_reply = True  # Track that we are in a reply loop
                    return  # Don't reset to IDLE below
                
                self.waiting_for_reply = False
                
            except sr.UnknownValueError:
                print("Google STT could not understand audio")
                # If we were in an await_reply loop, just keep waiting instead of aborting
                if getattr(self, "waiting_for_reply", False):
                    print("[Backend] Keeping microphone open for reply...")
                    self.state = "LISTENING"
                    self.audio_buffer = bytearray()
                    self.consecutive_silence_chunks = 0
                    self.grace_chunks_remaining = 0
                    self.waiting_for_voice = True
                    return
                # Otherwise, it was a wake word trigger that failed, reset to idle
                await websocket.send(json.dumps({
                    "type": "response",
                    "payload": {"text": "Sorry, I didn't catch that.", "app": ""}
                }))
                await asyncio.sleep(3)
                await websocket.send(json.dumps({"type": "status", "state": "state-idle"}))
                
            except sr.RequestError as e:
                print(f"Could not request results from Google; {e}")
                await websocket.send(json.dumps({
                    "type": "response",
                    "payload": {"text": "Network error processing speech.", "app": ""}
                }))
                await asyncio.sleep(3)
                await websocket.send(json.dumps({"type": "status", "state": "state-idle"}))

        except Exception as e:
            print(f"Transcription error: {e}")
            await websocket.send(json.dumps({
                "type": "response",
                "payload": {"text": "Internal error.", "app": ""}
            }))
            await asyncio.sleep(3)
            await websocket.send(json.dumps({"type": "status", "state": "state-idle"}))

        # Reset state
        self.state = "IDLE"
        self.audio_buffer = bytearray()


async def main_handler(websocket):
    print("Electron App Connected!")
    assistant = VoiceAssistant()
    
    # Initialize SubAgentManager for local testing
    async def sub_agent_notify(msg: dict):
        try:
            await websocket.send(json.dumps(msg))
        except Exception as e:
            print(f"[SubAgent WS Error] {e}")

    sub_manager = SubAgentManager(notify_callback=sub_agent_notify)
    set_sub_agent_manager(sub_manager, ws_callback=sub_agent_notify)
    
    # Initialize UI
    try:
        await websocket.send(json.dumps({"type": "status", "state": "state-idle"}))
        
        async for message in websocket:
            try:
                data = json.loads(message)
                msg_type = data.get("type")
                
                if msg_type == "audio_chunk":
                    await assistant.handle_audio_chunk(websocket, data.get("payload", ""))
                    
                elif msg_type == "hotkey_pressed":
                    print("=> HOTKEY PRESSED. Forcing wake...")
                    assistant.state = "LISTENING"
                    assistant.audio_buffer = bytearray()
                    await websocket.send(json.dumps({
                        "type": "status", "state": "state-listening"
                    }))
                    
            except json.JSONDecodeError:
                pass
            except Exception as inner_e:
                print(f"Error handling message: {inner_e}")
                
    except websockets.exceptions.ConnectionClosed as e:
        print(f"Electron disconnected: {e}")
    except Exception as e:
        print(f"Unexpected websocket error: {e}")


async def main():
    if PICOVOICE_ACCESS_KEY == "YOUR_PICOVOICE_ACCESS_KEY_HERE":
        print("!" * 60)
        print("ACTION REQUIRED: You must set PICOVOICE_ACCESS_KEY in ")
        print("backend_server.py to enable the wake word.")
        print("!" * 60)

    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if not gemini_key:
        print("!" * 60)
        print("NOTE: GEMINI_API_KEY not set. Agent will run in fallback mode.")
        print("Set it with: export GEMINI_API_KEY='your-key-here'")
        print("!" * 60)
        
    async with websockets.serve(main_handler, "127.0.0.1", 8000, origins=None):
        print("Server running on ws://127.0.0.1:8000 (Allow All Origins)")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
