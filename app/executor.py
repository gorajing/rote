"""Executes ONE Gemini CU action on a Playwright page. Pure execution, no model calls.
Faithful to the Gemini 3.5 Flash browser action space (ai.google.dev/gemini-api/docs/computer-use).
Coordinates arrive normalized 0-999; denormalize against the locked viewport."""
import time
from .config import VIEWPORT


def _dx(x, w=VIEWPORT[0]):
    return int(x / 1000 * w)


def _dy(y, h=VIEWPORT[1]):
    return int(y / 1000 * h)


def execute_action(page, fname: str, args: dict) -> dict:
    """Run one function_call. Returns {} on success or {'error': ...} on failure."""
    try:
        if fname in ("open_web_browser", "open_app", "take_screenshot"):
            pass  # no-op / handled by the loop's screenshot
        elif fname in ("click", "double_click", "triple_click", "middle_click",
                       "right_click", "move", "mouse_down", "mouse_up"):
            x, y = _dx(args["x"]), _dy(args["y"])
            if fname == "click":
                page.mouse.click(x, y)
            elif fname == "double_click":
                page.mouse.dblclick(x, y)
            elif fname == "triple_click":
                page.mouse.click(x, y, click_count=3)
            elif fname == "right_click":
                page.mouse.click(x, y, button="right")
            elif fname == "middle_click":
                page.mouse.click(x, y, button="middle")
            elif fname == "move":
                page.mouse.move(x, y)
            elif fname == "mouse_down":
                page.mouse.move(x, y); page.mouse.down()
            elif fname == "mouse_up":
                page.mouse.move(x, y); page.mouse.up()
        elif fname == "type":
            if "x" in args and "y" in args:
                page.mouse.click(_dx(args["x"]), _dy(args["y"]))
            page.keyboard.press("Meta+A")  # clear field first
            page.keyboard.press("Backspace")
            page.keyboard.type(args["text"])
            if args.get("press_enter"):
                page.keyboard.press("Enter")
        elif fname == "scroll":
            x, y = _dx(args.get("x", 500)), _dy(args.get("y", 500))
            mag = args.get("magnitude_in_pixels", 300)
            ddx, ddy = {"up": (0, -mag), "down": (0, mag),
                        "left": (-mag, 0), "right": (mag, 0)}[args["direction"]]
            page.mouse.move(x, y); page.mouse.wheel(ddx, ddy)
        elif fname == "press_key":
            page.keyboard.press(args["key"])
        elif fname == "key_down":
            page.keyboard.down(args["key"])
        elif fname == "key_up":
            page.keyboard.up(args["key"])
        elif fname == "hotkey":
            page.keyboard.press("+".join(args["keys"]))
        elif fname == "drag_and_drop":
            page.mouse.move(_dx(args["start_x"]), _dy(args["start_y"])); page.mouse.down()
            page.mouse.move(_dx(args["end_x"]), _dy(args["end_y"])); page.mouse.up()
        elif fname == "navigate":
            page.goto(args["url"])
        elif fname == "go_back":
            page.go_back()
        elif fname == "go_forward":
            page.go_forward()
        elif fname == "wait":
            time.sleep(args.get("seconds", 1))
        else:
            return {"error": f"unhandled action: {fname}"}

        page.wait_for_load_state(timeout=5000)
        time.sleep(0.4)
        return {}
    except Exception as e:
        return {"error": str(e)}
