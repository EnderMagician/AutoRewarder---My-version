"""Human-like interaction helpers for Selenium-driven browsing."""

import random
import time
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.actions.action_builder import ActionBuilder
from selenium.webdriver.common.actions.pointer_input import PointerInput
from selenium.webdriver.common.actions.wheel_input import ScrollOrigin

from ..utils import human_typing

# Pixels per mouse-wheel notch, as Chromium scrolls on Windows.
WHEEL_NOTCH = 100


class HumanBehavior:
    """
    Simulates human-like behavior when interacting with a web page.

    Two modes:
    - desktop (mobile=False): mouse pointer, Bezier trajectories, wheel scroll
      via JS scrollBy, click = mousedown/mouseup.
    - mobile  (mobile=True):  touch pointer, no hover before tap (touch
      pointers don't emit hover events), direct tap at element center with
      a short natural hold, scroll via swipe gesture (pointerdown -> move ->
      pointerup) instead of JS scrollBy.
    """

    def __init__(self, driver, show_cursor=False, mobile=False):
        """
        Args:
            driver (webdriver): The Selenium WebDriver instance.
            show_cursor (bool): Whether to draw a debug cursor overlay. It is
                a <div> injected into the page, which any script on the page
                can see, so it is off unless the user turns it on.
            mobile (bool): Whether to emit touch gestures instead of mouse
                events. Pair with a driver that has `Emulation.setTouchEmulationEnabled`
                enabled (done automatically by DriverManager when mobile=True).
        """

        self.driver = driver
        self.show_cursor = show_cursor
        self.mobile = bool(mobile)
        # Stored in viewport coordinates (not document coordinates).
        self.last_mouse_position = [random.randint(100, 500), random.randint(100, 500)]
        # How fast this person is today: a multiplier on typing and reading
        # pauses (>1 is slower). Thumbs type slower than ten fingers.
        self.tempo = random.uniform(0.85, 1.3)
        self.typing_tempo = self.tempo * (1.2 if self.mobile else 1.0)

    def type_text(self, element, text):
        """Type `text` into `element` at this session's typing speed."""
        human_typing(element, text, tempo=self.typing_tempo)

    def pause(self, low, high):
        """Sleep a random time between `low` and `high`, scaled by tempo."""
        time.sleep(random.uniform(low, high) * self.tempo)

    def _new_actions(self):
        """
        Build an ActionChains bound to the right W3C pointer type.
        On mobile, the default mouse pointer is replaced with a touch pointer
        so every subsequent pointer_action emits touch events.
        """
        actions = ActionChains(self.driver)
        if self.mobile:
            touch = PointerInput(kind="touch", name="touch")
            actions.w3c_actions = ActionBuilder(self.driver, mouse=touch, duration=0)
        return actions

    def _draw_debug_cursor(self, x, y, color="red"):
        """
        Draw a debug cursor on the page

        Args:
            x (int): The x-coordinate of the cursor in viewport coordinates.
            y (int): The y-coordinate of the cursor in viewport coordinates.
            color (str): The color of the cursor (default is "red").

        Note: This is a debug feature to visualize mouse movements.
        It creates a small circle that follows the mouse position.
        It does not affect the actual mouse events and is purely for visual debugging purposes.
        """

        if not self.show_cursor:
            return

        # Fixed-position overlay cursor; doesn't intercept clicks (pointer-events: none).
        script = f"""
        let cursor = document.getElementById('selenium-bot-cursor');
        if (!cursor) {{
            cursor = document.createElement('div');
            cursor.id = 'selenium-bot-cursor';
            cursor.style.width = '12px';
            cursor.style.height = '12px';
            cursor.style.background = '{color}';
            cursor.style.position = 'fixed';
            cursor.style.borderRadius = '50%';
            cursor.style.zIndex = '9999999';
            cursor.style.pointerEvents = 'none'; // Allow clicks to pass through
            cursor.style.boxShadow = '0 0 5px rgba(0,0,0,0.5)';
            cursor.style.transition = 'background 0.2s';
            document.body.appendChild(cursor);
        }}
        // Center the cursor on the (x, y) position
        cursor.style.left = (arguments[0] - 6) + 'px';
        cursor.style.top = (arguments[1] - 6) + 'px';
        """
        self.driver.execute_script(script, x, y)

    def _ease_in_out(self, t):
        """
        Ease in-out function for smoother mouse movement (smoothstep)

        Args:
            t (float): A value between 0 and 1 representing the progress of the movement.

        Returns:
            float: The eased value corresponding to the input progress.
        """
        return t * t * (3 - 2 * t)

    def _get_viewport_size(self):
        """
        Return viewport size (width, height) for coordinate clamping

        Returns:
            tuple: (viewport_width, viewport_height)
        """

        # Viewport = visible browser area, not the full document size.
        width, height = self.driver.execute_script(
            "return [window.innerWidth, window.innerHeight];"
        )
        return int(width), int(height)

    def _clamp_point(self, x, y, width, height):
        """
        Clamp a point to the viewport bounds

        Args:
            x (int): The x-coordinate of the point.
            y (int): The y-coordinate of the point.
            width (int): The width of the viewport.
            height (int): The height of the viewport.

        Returns:
            tuple: (clamped_x, clamped_y)
        """

        # Prevent MoveTargetOutOfBounds errors.
        clamped_x = max(0, min(x, width - 1))
        clamped_y = max(0, min(y, height - 1))
        return clamped_x, clamped_y

    def scroll_page(self):
        """
        Scroll down the page the way a person reads it.

        Depth follows typical reading behavior: 70% of the time 10-50% of
        the page, otherwise most or all of it. On desktop the page moves
        with real mouse-wheel input in short bursts of notches, with reading
        pauses in between, the occasional scroll back up and a little mouse
        movement. On mobile it swipes.
        """

        if self.mobile:
            self._swipe_scroll()
            return

        if random.random() < 0.7:
            depth = random.uniform(0.1, 0.5)
        else:
            depth = random.uniform(0.67, 1.0)

        try:
            page_height, view_height = self.driver.execute_script(
                "return [document.documentElement.scrollHeight, window.innerHeight];"
            )
        except Exception:
            page_height, view_height = 4000, 900
        target = max(0, int(page_height * depth) - int(view_height))

        bursts = 0
        while bursts < 12:
            bursts += 1
            notches = random.randint(2, 6)
            self._wheel(notches * WHEEL_NOTCH, notches)

            if random.random() < 0.3:
                self._drift_mouse()
            self.pause(0.6, 2.4)

            # Went past something; scroll back up a little to re-read it.
            if random.random() < 0.12:
                back = random.randint(1, 3)
                self._wheel(-back * WHEEL_NOTCH, back)
                self.pause(0.8, 2.0)

            try:
                scrolled = self.driver.execute_script("return window.scrollY;")
            except Exception:
                break
            if scrolled >= target:
                break

        # Final read of what's on screen.
        self.pause(1.5, 4.0)

    def _wheel(self, delta_y, notches):
        """
        Scroll `delta_y` pixels as `notches` wheel ticks under the mouse.

        Each tick is its own wheel event, the way a wheel clicks, with tens of
        milliseconds between them. Falls back to window.scrollBy if the
        driver rejects wheel input.
        """
        width, height = self._get_viewport_size()
        x, y = self._clamp_point(*self.last_mouse_position, width, height)
        step = int(delta_y / max(1, notches))
        for _ in range(max(1, notches)):
            try:
                ActionChains(self.driver).scroll_from_origin(
                    ScrollOrigin.from_viewport(int(x), int(y)), 0, step
                ).perform()
            except Exception:
                try:
                    self.driver.execute_script(
                        "window.scrollBy(0, arguments[0]);", step
                    )
                except Exception:
                    return
            time.sleep(random.uniform(0.035, 0.11))

    def _drift_mouse(self):
        """
        Move the mouse a short way across the page, as hands do while reading.

        A few small, eased steps toward a nearby point; nothing is clicked.
        """
        width, height = self._get_viewport_size()
        start_x, start_y = self._clamp_point(*self.last_mouse_position, width, height)
        target_x, target_y = self._clamp_point(
            start_x + random.randint(-220, 220),
            start_y + random.randint(-140, 140),
            width,
            height,
        )
        steps = random.randint(6, 14)
        x, y = start_x, start_y
        for i in range(1, steps + 1):
            t = self._ease_in_out(i / steps)
            x = int(start_x + (target_x - start_x) * t + random.randint(-1, 1))
            y = int(start_y + (target_y - start_y) * t + random.randint(-1, 1))
            x, y = self._clamp_point(x, y, width, height)
            try:
                actions = ActionChains(self.driver)
                actions.w3c_actions.pointer_action.move_to_location(x, y)
                actions.perform()
            except Exception:
                break
            self._draw_debug_cursor(x, y)
            time.sleep(random.uniform(0.012, 0.045))
        self.last_mouse_position = [x, y]

    def move_to_element(
        self, element, steps=None, retries_left=1, scroll_into_view=True
    ):
        """
        Move mouse to the given element in a human-like manner

        Args:
            element (WebElement): The target element to move the mouse to.
            steps (int): The number of steps for the movement. If None, it will be calculated based on distance.
            retries_left (int): The number of retries left for "missed" movements. Default is 1.
            scroll_into_view (bool): Whether to scroll the element into view if it's outside the viewport. Default is True.
        """

        start_x, start_y = self.last_mouse_position
        viewport_width, viewport_height = self._get_viewport_size()
        start_x, start_y = self._clamp_point(
            start_x, start_y, viewport_width, viewport_height
        )
        self.last_mouse_position = [start_x, start_y]

        # IMPORTANT: Scrolling during mouse movement can cause visible "jumping"
        # on rewards.bing.com. Only scroll when explicitly allowed and when the element
        # is outside the viewport.
        if scroll_into_view:
            try:
                needs_scroll = bool(
                    self.driver.execute_script(
                        """
                    const el = arguments[0];
                    if (!el) return true;
                    const rect = el.getBoundingClientRect();
                    const viewH = window.innerHeight || document.documentElement.clientHeight;
                    const viewW = window.innerWidth || document.documentElement.clientWidth;
                    return (rect.bottom <= 0) || (rect.top >= viewH) || (rect.right <= 0) || (rect.left >= viewW);
                    """,
                        element,
                    )
                )
            except Exception:
                needs_scroll = True

            if needs_scroll:
                try:
                    self.driver.execute_script(
                        "document.documentElement.style.scrollBehavior='auto';"
                        "document.body.style.scrollBehavior='auto';"
                    )
                except Exception:
                    pass

                self.driver.execute_script(
                    "arguments[0].scrollIntoView({block: 'nearest', inline: 'nearest'});",
                    element,
                )
                time.sleep(random.uniform(0.05, 0.2))

        elem_left, elem_top, elem_width, elem_height = self.driver.execute_script(
            """
            const rect = arguments[0].getBoundingClientRect();
            return [rect.left, rect.top, rect.width, rect.height];
            """,
            element,
        )

        # getBoundingClientRect() returns coordinates relative to the viewport.
        # Use viewport-origin pointer moves (W3C) to avoid implicit scrolling.
        # Move to the center of the element with some random offset
        elem_width = max(1, int(elem_width))
        elem_height = max(1, int(elem_height))
        max_offset_x = max(0, elem_width - 1)
        max_offset_y = max(0, elem_height - 1)
        target_x = int(elem_left) + random.randint(0, max_offset_x)
        target_y = int(elem_top) + random.randint(0, max_offset_y)

        # Sometimes make a "miss" (only if have retries left)
        miss = (random.random() < 0.05) and (retries_left > 0)
        if miss:
            target_x += random.randint(-30, 30)
            target_y += random.randint(-30, 30)

        target_x, target_y = self._clamp_point(
            target_x, target_y, viewport_width, viewport_height
        )

        # Control point (for Bezier curve)
        control_x = (start_x + target_x) / 2 + random.randint(-150, 150)
        control_y = (start_y + target_y) / 2 + random.randint(-150, 150)

        if steps is None:
            distance = ((target_x - start_x) ** 2 + (target_y - start_y) ** 2) ** 0.5
            steps = int(distance / random.uniform(8, 15))
            steps = max(10, min(40, steps))

        last_x, last_y = start_x, start_y

        for i in range(steps + 1):
            t = i / steps
            t = self._ease_in_out(t)

            curr_x = int(
                (1 - t) ** 2 * start_x + 2 * (1 - t) * t * control_x + t**2 * target_x
            )
            curr_y = int(
                (1 - t) ** 2 * start_y + 2 * (1 - t) * t * control_y + t**2 * target_y
            )

            # Micro-vibration
            curr_x += random.randint(-2, 2)
            curr_y += random.randint(-2, 2)

            curr_x, curr_y = self._clamp_point(
                curr_x, curr_y, viewport_width, viewport_height
            )

            delta_x = curr_x - last_x
            delta_y = curr_y - last_y

            # Move the mouse to an absolute point within the viewport
            if delta_x != 0 or delta_y != 0:
                try:
                    actions = ActionChains(self.driver)
                    # W3C Pointer: absolute move to a viewport coordinate.
                    actions.w3c_actions.pointer_action.move_to_location(
                        int(curr_x), int(curr_y)
                    )
                    actions.perform()
                except Exception:
                    # If the driver rejects the move for any reason, keep going without
                    # falling back to move_to_element(element) (that can scroll the page).
                    pass

            # Draw debug cursor at the current position
            self._draw_debug_cursor(curr_x, curr_y)

            last_x, last_y = curr_x, curr_y

            # Variable pause to mimic human movement speed changes
            if i < steps * 0.3:
                pause = random.uniform(0.005, 0.02)
            elif i < steps * 0.7:
                pause = random.uniform(0.01, 0.04)
            else:
                pause = random.uniform(0.02, 0.06)

            if random.random() < 0.05:
                pause += random.uniform(0.05, 0.15)

            time.sleep(pause)

        # If missed the target, do a quick correction move
        if miss:
            time.sleep(random.uniform(0.05, 0.2))
            # Correction pass: retry toward the same element (keeps the same scroll policy).
            self.move_to_element(
                element,
                steps=random.randint(5, 10),
                retries_left=retries_left - 1,
                scroll_into_view=scroll_into_view,
            )
            return

        # Final micro-correction before clicking
        for _ in range(random.randint(1, 3)):
            delta_x = random.randint(-2, 2)
            delta_y = random.randint(-2, 2)
            last_x += delta_x
            last_y += delta_y
            last_x, last_y = self._clamp_point(
                last_x, last_y, viewport_width, viewport_height
            )
            try:
                actions = ActionChains(self.driver)
                actions.w3c_actions.pointer_action.move_to_location(
                    int(last_x), int(last_y)
                )
                actions.perform()
            except Exception:
                pass
            self._draw_debug_cursor(last_x, last_y)
            time.sleep(random.uniform(0.01, 0.03))

        time.sleep(random.uniform(0.1, 0.4))
        self.last_mouse_position = [last_x, last_y]

    def click_element(self, element, scroll_into_view=True):
        """
        Full cycle: move to element, highlight in green (click), then click.

        On mobile, this skips the Bezier-curve mouse travel (touch pointers
        don't emit hover events) and performs a single tap gesture at the
        element's center.

        Args:
            element (WebElement): The target element to click.
            scroll_into_view (bool): Whether to scroll the element into view
                if it's outside the viewport. Default is True.
        """

        if self.mobile:
            self._tap_element(element, scroll_into_view=scroll_into_view)
            return

        self.move_to_element(element, scroll_into_view=scroll_into_view)
        time.sleep(random.uniform(0.1, 0.3))

        # Change the cursor color to green at the moment of clicking
        self._draw_debug_cursor(
            self.last_mouse_position[0], self.last_mouse_position[1], color="green"
        )

        # Perform a real pointer click at the current mouse position (viewport coords).
        # This avoids WebElement.click() interactability checks that often fail on
        # dynamic pages (e.g., rewards.bing.com) even when the user-visible click works.
        x, y = self.last_mouse_position
        try:
            actions = ActionChains(self.driver)
            actions.w3c_actions.pointer_action.move_to_location(int(x), int(y))
            actions.w3c_actions.pointer_action.click()
            actions.perform()
        except WebDriverException:
            # Fallbacks: JS click first, then WebElement.click as last resort.
            try:
                self.driver.execute_script("arguments[0].click();", element)
            except Exception:
                element.click()
        time.sleep(0.2)

        # Return the cursor color to red after clicking
        self._draw_debug_cursor(
            self.last_mouse_position[0], self.last_mouse_position[1], color="red"
        )

    # ---------------------------------------------------------------------
    # Mobile-only helpers (touch gestures)
    # ---------------------------------------------------------------------

    def _tap_element(self, element, scroll_into_view=True):
        """
        Tap an element with a real touch gesture: touchstart at a jittered
        point within the element's rect, a natural hold (50-150 ms), then
        touchend. No Bezier travel — a finger doesn't hover before landing.

        Args:
            element (WebElement): The target element to tap.
            scroll_into_view (bool): Whether to scroll the element into view
                if it's outside the viewport. Default is True.
        """
        if scroll_into_view:
            try:
                needs_scroll = bool(
                    self.driver.execute_script(
                        """
                        const el = arguments[0];
                        if (!el) return true;
                        const r = el.getBoundingClientRect();
                        const vh = window.innerHeight || document.documentElement.clientHeight;
                        const vw = window.innerWidth  || document.documentElement.clientWidth;
                        return (r.bottom <= 0) || (r.top >= vh) || (r.right <= 0) || (r.left >= vw);
                        """,
                        element,
                    )
                )
            except Exception:
                needs_scroll = True

            if needs_scroll:
                try:
                    self.driver.execute_script(
                        "arguments[0].scrollIntoView({block: 'center', inline: 'nearest'});",
                        element,
                    )
                    # Wait for the scroll to settle — mobile scroll is momentum-based.
                    time.sleep(random.uniform(0.35, 0.8))
                except Exception:
                    pass

        # Element rect (viewport coords).
        try:
            rect = self.driver.execute_script(
                "const r = arguments[0].getBoundingClientRect();"
                "return [r.left, r.top, r.width, r.height];",
                element,
            )
            elem_left, elem_top, elem_width, elem_height = rect
        except Exception:
            # Element detached; fall back to a JS click.
            try:
                self.driver.execute_script("arguments[0].click();", element)
            except Exception:
                element.click()
            return

        elem_width = max(1, int(elem_width))
        elem_height = max(1, int(elem_height))

        # Pick a realistic tap point: somewhere around the center with a
        # pad so we don't always land on the exact pixel center (a finger
        # has a fat touch surface; real taps are inside the target, not
        # geometrically perfect).
        pad_x = max(1, elem_width // 3)
        pad_y = max(1, elem_height // 3)
        tap_x = int(elem_left + elem_width / 2 + random.randint(-pad_x, pad_x))
        tap_y = int(elem_top + elem_height / 2 + random.randint(-pad_y, pad_y))

        vw, vh = self._get_viewport_size()
        tap_x, tap_y = self._clamp_point(tap_x, tap_y, vw, vh)

        self._draw_debug_cursor(tap_x, tap_y, color="green")

        try:
            actions = self._new_actions()
            actions.w3c_actions.pointer_action.move_to_location(tap_x, tap_y)
            actions.w3c_actions.pointer_action.pointer_down()
            # Natural tap hold duration.
            actions.w3c_actions.pointer_action.pause(random.uniform(0.05, 0.14))
            actions.w3c_actions.pointer_action.pointer_up()
            actions.perform()
        except WebDriverException:
            try:
                self.driver.execute_script("arguments[0].click();", element)
            except Exception:
                element.click()

        self.last_mouse_position = [tap_x, tap_y]
        time.sleep(random.uniform(0.18, 0.45))
        self._draw_debug_cursor(tap_x, tap_y, color="red")

    def _swipe_scroll(self):
        """
        Scroll by emitting one or more swipe-up gestures (touchstart near the
        bottom of the viewport, touchmove upward, touchend). Matches the
        original scroll_page distribution: 70% short browse, 30% deep scroll.
        """
        width, height = self._get_viewport_size()

        # Match original ratio: 70% small scroll (1-2 swipes), 30% deep (2-4).
        if random.random() < 0.7:
            num_swipes = random.randint(1, 2)
        else:
            num_swipes = random.randint(2, 4)

        for index in range(num_swipes):
            x = width // 2 + random.randint(-width // 6, width // 6)
            y_start = int(height * random.uniform(0.70, 0.88))
            y_end = int(height * random.uniform(0.12, 0.30))
            # Now and then swipe back down to look at something again.
            if index > 0 and random.random() < 0.15:
                y_start, y_end = int(height * random.uniform(0.25, 0.4)), int(
                    height * random.uniform(0.55, 0.7)
                )

            x, y_start = self._clamp_point(x, y_start, width, height)
            x, y_end = self._clamp_point(x, y_end, width, height)

            try:
                actions = self._new_actions()
                actions.w3c_actions.pointer_action.move_to_location(x, y_start)
                actions.w3c_actions.pointer_action.pointer_down()

                steps = random.randint(8, 14)
                for i in range(1, steps + 1):
                    t = self._ease_in_out(i / steps)
                    y = int(y_start + (y_end - y_start) * t)
                    x_jit = x + random.randint(-3, 3)
                    x_jit, y = self._clamp_point(x_jit, y, width, height)
                    actions.w3c_actions.pointer_action.move_to_location(x_jit, y)
                    actions.w3c_actions.pointer_action.pause(
                        random.uniform(0.010, 0.028)
                    )

                actions.w3c_actions.pointer_action.pointer_up()
                actions.perform()
            except WebDriverException:
                # Fallback: JS scroll if the touch gesture rejects.
                try:
                    self.driver.execute_script(
                        f"window.scrollBy(0, {y_start - y_end});"
                    )
                except Exception:
                    pass

            # Pause between swipes (inertia + reading time).
            self.pause(0.5, 1.6)

        # Final "reading" pause.
        self.pause(2.5, 5.5)
