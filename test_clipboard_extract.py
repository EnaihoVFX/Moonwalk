import asyncio
import subprocess

async def extract_via_clipboard():
    print("Activating Chrome...")
    subprocess.run(["osascript", "-e", 'tell application "Google Chrome" to activate'])
    await asyncio.sleep(0.5)
    
    print("Select All...")
    subprocess.run(["osascript", "-e", 'tell application "System Events" to keystroke "a" using command down'])
    await asyncio.sleep(0.5)
    
    print("Copying...")
    subprocess.run(["osascript", "-e", 'tell application "System Events" to keystroke "c" using command down'])
    await asyncio.sleep(0.5)
    
    print("Deselecting...")
    subprocess.run(["osascript", "-e", 'tell application "System Events" to key code 124']) # right arrow
    await asyncio.sleep(0.5)
    
    print("Reading clipboard...")
    result = subprocess.run(["pbpaste"], capture_output=True, text=True)
    print("Extracted Length:", len(result.stdout))
    print("Preview:", result.stdout[:200])

if __name__ == "__main__":
    asyncio.run(extract_via_clipboard())
