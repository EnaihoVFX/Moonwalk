import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

async def test():
    from backend.tools.gworkspace_tools import _bridge_js
    js = "document.querySelector('.kix-appview-editor')?.innerText?.substring(0,8000) || document.body.innerText.substring(0,8000)"
    res = await _bridge_js(js)
    print("Result:", repr(res))

asyncio.run(test())
