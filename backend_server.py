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

# ==========================================
# IMPORTANT: PASTE YOUR PICOVOICE ACCESS KEY HERE
PICOVOICE_ACCESS_KEY = "lDvqq7J641WbqdzMsPCdLlawELhfGZOGhaceFzl3ZYYYzeeuXq55YA=="
# ==========================================

class VoiceAssistant:
    def __init__(self):
        self.state = "IDLE"  # IDLE, LISTENING, LOADING, DOING
        self.porcupine = None
        
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
                    print(f"Porcupine initialized with CUSTOM wake word from: {custom_ppn}")
                    print("Waiting for wake word 'Hey Moonwalk'...")
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

    async def handle_audio_chunk(self, websocket, b64_payload):
        """Decode base64 WAV, strip header, get raw PCM bytes."""
        try:
            print(f"Received chunk of len: {len(b64_payload)}")
            wav_bytes = base64.b64decode(b64_payload)
            
            # Use Python's wave module to read the WAV chunk
            with wave.open(io.BytesIO(wav_bytes), 'rb') as w:
                pcm_data = w.readframes(w.getnframes())

            print(f"Decoded PCM len: {len(pcm_data)}")
            
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

        # Porcupine expects 512 samples per frame (16-bit PCM = 1024 bytes)
        # We chunk the incoming PCM data into 1024-byte chunks
        chunk_size = 1024
        for i in range(0, len(pcm_data), chunk_size):
            chunk = pcm_data[i:i+chunk_size]
            if len(chunk) == chunk_size:
                # Convert bytes to tuple of Int16
                pcm_tuple = struct.unpack_from("h" * self.porcupine.frame_length, chunk)
                
                keyword_index = self.porcupine.process(pcm_tuple)
                if keyword_index >= 0:
                    print("=> WAKE WORD DETECTED!")
                    self.state = "LISTENING"
                    self.audio_buffer = bytearray() # Clear buffer for new command
                    await websocket.send(json.dumps({"state": "LISTENING"}))
                    # Stop processing this chunk and wait for more to buffer the command
                    break

    async def buffer_command(self, websocket, pcm_data):
        """Buffer incoming audio while listening, detect silence to stop."""
        self.audio_buffer.extend(pcm_data)

        # Simple silence detection using RMS amplitude
        # Calculate RMS of the incoming chunk
        ints = np.frombuffer(pcm_data, dtype=np.int16)
        if len(ints) > 0:
            rms = np.sqrt(np.mean(ints**2))
            
            # If the buffer has some length, and RMS drops below a threshold, assume they finished talking
            if len(self.audio_buffer) > 32000 and rms < 300: # Tweaked thresholds
                print("=> SILENCE DETECTED. Processing Command...")
                self.state = "LOADING"
                await websocket.send(json.dumps({"state": "LOADING"}))
                
                # Start processing the audio asynchronously without blocking the loop too badly
                asyncio.create_task(self.transcribe_command(websocket))

    async def transcribe_command(self, websocket):
        """Run Speech-to-Text on the buffered audio."""
        print(f"Transcribing {len(self.audio_buffer)} bytes of audio...")
        
        try:
            # We need to wrap the raw PCM buffer back into a WAV structure for SpeechRecognition
            wav_io = io.BytesIO()
            with wave.open(wav_io, 'wb') as w:
                w.setnchannels(1)
                w.setsampwidth(2) # 16-bit
                w.setframerate(16000)
                w.writeframes(self.audio_buffer)
            wav_io.seek(0)

            # Recognize using Google Web Speech API (free)
            with sr.AudioFile(wav_io) as source:
                audio_data = self.recognizer.record(source)
                
            try:
                # Run actual API call (in real app, use loop.run_in_executor since this is blocking)
                text = self.recognizer.recognize_google(audio_data)
                print(f"=> TRANSCRIBED: {text}")
                
                # Send back the result
                self.state = "DOING"
                await websocket.send(json.dumps({
                    "state": "DOING",
                    "text": f"You said: {text}"
                }))
                
            except sr.UnknownValueError:
                print("Google STT could not understand audio")
                await websocket.send(json.dumps({
                    "state": "DOING",
                    "text": "Sorry, I didn't catch that."
                }))
            except sr.RequestError as e:
                print(f"Could not request results from Google; {e}")
                await websocket.send(json.dumps({
                    "state": "DOING",
                    "text": "Network error processing speech."
                }))

        except Exception as e:
            print(f"Transcription error: {e}")
            await websocket.send(json.dumps({
                "state": "DOING",
                "text": "Internal Error."
            }))

        # Reset states back to IDLE
        await asyncio.sleep(3) # Let UI show the response
        self.state = "IDLE"
        self.audio_buffer = bytearray()
        await websocket.send(json.dumps({"state": "IDLE"}))


async def main_handler(websocket):
    print("Electron App Connected!")
    assistant = VoiceAssistant()
    
    # Initialize UI
    try:
        await websocket.send(json.dumps({"state": "IDLE"}))
        
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
                    await websocket.send(json.dumps({"state": "LISTENING"}))
                    
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
        print("backend_server.py line 12 to enable the wake word.")
        print("!" * 60)
        
    async with websockets.serve(main_handler, "127.0.0.1", 8000, origins=None):
        print("Server running on ws://127.0.0.1:8000 (Allow All Origins)")
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
