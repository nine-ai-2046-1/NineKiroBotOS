"""
browser_agent.py — Universal background browser agent
Usage: python3 browser_agent.py [profile_dir] [headless]
       python3 browser_agent.py ./fb_profile false

Communicates via files:
  /tmp/browser_cmd.txt    — write a command here to execute
  /tmp/browser_result.txt — read result after execution
  /tmp/browser_status.txt — current status (ready/busy/error)
  /tmp/browser.pid        — PID of this process
"""
import asyncio
import os
import sys
import time
import traceback
from pathlib import Path
from playwright.async_api import async_playwright

CMD_FILE    = "/tmp/browser_cmd.txt"
RESULT_FILE = "/tmp/browser_result.txt"
STATUS_FILE = "/tmp/browser_status.txt"
PID_FILE    = "/tmp/browser.pid"

PROFILE_DIR = sys.argv[1] if len(sys.argv) > 1 else "./browser_profile"
HEADLESS    = sys.argv[2].lower() != "false" if len(sys.argv) > 2 else False

def write_status(s: str):
    open(STATUS_FILE, "w").write(s)
    print(f"[status] {s}", flush=True)

def write_result(r: str):
    open(RESULT_FILE, "w").write(r)
    print(f"[result] {r[:200]}", flush=True)

async def main():
    # Write PID
    open(PID_FILE, "w").write(str(os.getpid()))

    Path(PROFILE_DIR).mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            headless=HEADLESS,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 900}
        )
        page = await browser.new_page()
        write_status("ready")
        print(f"Browser ready | profile={PROFILE_DIR} headless={HEADLESS}", flush=True)

        while True:
            if os.path.exists(CMD_FILE):
                cmd = open(CMD_FILE).read().strip()
                os.remove(CMD_FILE)
                if not cmd:
                    continue

                write_status("busy")
                print(f"[cmd] {cmd[:200]}", flush=True)

                try:
                    # Execute the command as Python code with `page` and `browser` in scope
                    local_vars = {"page": page, "browser": browser, "result": None}
                    exec(compile(cmd, "<cmd>", "exec"), local_vars)
                    # If cmd is async, run it
                    if asyncio.iscoroutine(local_vars.get("result")):
                        local_vars["result"] = await local_vars["result"]
                    result = str(local_vars.get("result", "done"))
                    write_result(result)
                except Exception:
                    write_result(f"ERROR:\n{traceback.format_exc()}")

                write_status("ready")
            else:
                await asyncio.sleep(0.5)

if __name__ == "__main__":
    asyncio.run(main())
