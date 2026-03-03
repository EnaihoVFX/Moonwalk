import asyncio
import json
import websockets

async def handler(websocket):
    print("[Mock Backend] Client connected!")
    try:
        async for message_str in websocket:
            msg = json.loads(message_str)
            
            # Simulate a full task flow if it's an audio chunk or a manual wake
            if msg.get("type") == "audio_chunk" or msg.get("action") == "wake_up":
                print("[Mock Backend] Triggering mock sequence...")
                
                # 1. State: Listening (Blue Mic)
                await websocket.send(json.dumps({"type": "status", "state": "state-listening"}))
                await asyncio.sleep(1.2) 

                # 2. State: Thinking (Bouncing Dots ONLY - No text)
                await websocket.send(json.dumps({
                    "type": "progress",
                    "state": "state-loading"
                }))
                await asyncio.sleep(1.5)

                # 3. State: Action (Spinner + Logo + Text)
                # Note: "Opening Google Chrome" is now shown here, immediately with the icon.
                await websocket.send(json.dumps({
                    "type": "response",
                    "payload": {
                        "text": "Opening Google Chrome...",
                        "app": "google chrome"
                    }
                }))
                
                # Wait then reset
                await asyncio.sleep(6)
                await websocket.send(json.dumps({"type": "status", "state": "state-idle"}))

    except websockets.exceptions.ConnectionClosed:
        print("[Mock Backend] Client disconnected")

async def main():
    async with websockets.serve(handler, "127.0.0.1", 8000):
        print("[Mock Backend] Server running on ws://127.0.0.1:8000")
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
