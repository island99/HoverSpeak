#!/usr/bin/env python3
import os
import objc
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass

import ApplicationServices as AS
import AppKit
import Quartz
import Vision


POLL_INTERVAL = float(os.getenv("HOVERSPEAK_POLL", "0.28"))
UI_TICK_INTERVAL = float(os.getenv("HOVERSPEAK_UI_TICK", "0.03"))
REPEAT_COOLDOWN = float(os.getenv("HOVERSPEAK_COOLDOWN", "1.4"))
HOVER_DWELL = float(os.getenv("HOVERSPEAK_HOVER_DWELL", "0.55"))
HOVER_MOVE_TOLERANCE = float(os.getenv("HOVERSPEAK_HOVER_MOVE_TOLERANCE", "10"))
VOICE = os.getenv("HOVERSPEAK_VOICE")
RATE = os.getenv("HOVERSPEAK_RATE")
AUTO_VOICE = os.getenv("HOVERSPEAK_AUTO_VOICE", "1") != "0"
DEFAULT_LATIN_RATE = os.getenv("HOVERSPEAK_LATIN_RATE", "175")
DEFAULT_CJK_RATE = os.getenv("HOVERSPEAK_CJK_RATE", "158")
CJK_VOICE_CANDIDATES = (
    "Tingting",
    "Eddy (中文（中国大陆）)",
    "Flo (中文（中国大陆）)",
    "Reed (中文（中国大陆）)",
    "Meijia",
)
LATIN_VOICE_CANDIDATES = (
    "Samantha",
    "Ava",
    "Eddy (英语（美国）)",
    "Reed (英语（美国）)",
    "Daniel",
)
TRIGGER_MODE = os.getenv("HOVERSPEAK_TRIGGER_MODE", "both").lower()
TRIGGER_OFF = "off"
TRIGGER_SELECTION = "selection"
TRIGGER_BOTH = "both"
TRIGGER_MODES = (TRIGGER_OFF, TRIGGER_SELECTION, TRIGGER_BOTH)
TRIGGER_ICON_NAMES = ("speaker.slash.fill", "textformat", "speaker.wave.2.fill")
TRIGGER_FALLBACK_MARKS = ("", "", "")
TRIGGER_TOOLTIPS = ("关闭发声", "只读选中文本", "选中和鼠标附近文本")
SWITCH_WIDTH = 58
SWITCH_HEIGHT = 20
SWITCH_PADDING_X = 2
SWITCH_PADDING_Y = 2
SWITCH_SEGMENT_WIDTH = 18
SWITCH_AUTO_HIDE_SECONDS = float(os.getenv("HOVERSPEAK_SWITCH_AUTO_HIDE", "2.5"))
CURSOR_REGION_DIAMETER = int(os.getenv("HOVERSPEAK_CURSOR_REGION", "82"))
OCR_LINE_MAX_CHARS = int(os.getenv("HOVERSPEAK_OCR_LINE_MAX_CHARS", "24"))
SELECTION_ENABLED = os.getenv("HOVERSPEAK_SELECTION", "1") != "0"
SELECTION_REPEAT_PAUSE = float(os.getenv("HOVERSPEAK_SELECTION_PAUSE", "0.8"))
SELECTION_MAX_CHARS = int(os.getenv("HOVERSPEAK_SELECTION_MAX_CHARS", "1200"))
SELECTION_COPY_MODE = os.getenv("HOVERSPEAK_SELECTION_COPY", "menu").lower()
SELECTION_COPY_INTERVAL = float(os.getenv("HOVERSPEAK_SELECTION_COPY_INTERVAL", "0.8"))
UNSAFE_KEYBOARD_COPY = os.getenv("HOVERSPEAK_UNSAFE_KEYBOARD_COPY", "0") == "1"
PROTECTED_KEYBOARD_COPY = os.getenv("HOVERSPEAK_PROTECTED_KEYBOARD_COPY", "1") != "0"
SELECTION_COPY_BLOCKED_BUNDLES = {
    bundle.strip()
    for bundle in os.getenv(
        "HOVERSPEAK_SELECTION_COPY_BLOCKED_BUNDLES",
        "com.apple.Safari,com.google.Chrome,com.google.Chrome.canary,com.microsoft.edgemac,com.brave.Browser,org.mozilla.firefox",
    ).split(",")
    if bundle.strip()
}
PROTECTED_KEYBOARD_COPY_BLOCKED_BUNDLES = {
    bundle.strip()
    for bundle in os.getenv(
        "HOVERSPEAK_PROTECTED_KEYBOARD_COPY_BLOCKED_BUNDLES",
        "com.apple.Safari,com.google.Chrome,com.google.Chrome.canary,com.microsoft.edgemac,com.brave.Browser,org.mozilla.firefox",
    ).split(",")
    if bundle.strip()
}
OCR_ENABLED = os.getenv("HOVERSPEAK_OCR", "1") != "0"
OCR_WIDTH = int(os.getenv("HOVERSPEAK_OCR_WIDTH", "360"))
OCR_HEIGHT = int(os.getenv("HOVERSPEAK_OCR_HEIGHT", "96"))
OCR_ACCURATE = os.getenv("HOVERSPEAK_OCR_ACCURATE", "1") != "0"
OCR_LANGUAGES = [lang.strip() for lang in os.getenv("HOVERSPEAK_OCR_LANGUAGES", "zh-Hans,en-US").split(",") if lang.strip()]
OCR_MIN_TEXT_HEIGHT = float(os.getenv("HOVERSPEAK_OCR_MIN_TEXT_HEIGHT", "0.015"))
OCR_Y_OFFSET = float(os.getenv("HOVERSPEAK_OCR_Y_OFFSET", "0"))
CJK_PHRASE_CHARS = int(os.getenv("HOVERSPEAK_CJK_CHARS", "2"))
DEBUG = os.getenv("HOVERSPEAK_DEBUG", "0") == "1"
CJK_RANGES = "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af"
CJK_RE = re.compile(f"[{CJK_RANGES}]")
LATIN_WORD_RE = re.compile(rf"[^\W_{CJK_RANGES}]+(?:['-][^\W_{CJK_RANGES}]+)*", re.UNICODE)
TOKEN_RE = re.compile(rf"[{CJK_RANGES}]|[^\W_{CJK_RANGES}]+(?:['-][^\W_{CJK_RANGES}]+)*", re.UNICODE)
BOUNDARY_RE = re.compile(rf"[\s,.;:!?，。！？；：、（）()\[\]{{}}<>《》\"'“”‘’]|[^\w{CJK_RANGES}]", re.UNICODE)
CHILD_ATTRIBUTES = (
    AS.kAXChildrenAttribute,
    AS.kAXVisibleChildrenAttribute,
    AS.kAXContentsAttribute,
    "AXChildrenInNavigationOrder",
)
SAFE_COPY_MODES = {"1", "true", "on", "yes", "menu", "safe"}
UNSAFE_COPY_MODES = {"1", "true", "on", "yes", "keyboard", "unsafe"}


@dataclass
class SpokenWord:
    text: str
    element_description: str
    spoken_at: float


class TriggerSwitchController(AppKit.NSObject):
    def initWithSpeaker_(self, speaker):
        self = objc.super(TriggerSwitchController, self).init()
        if self is None:
            return None

        self.speaker = speaker
        self.panel = None
        self.control = None
        self.current_selection_id = None
        self.dismissed_selection_id = None
        self.shown_at = 0.0
        self.was_clicked = False
        self.mouse_monitor = AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            AppKit.NSEventMaskLeftMouseDown | AppKit.NSEventMaskRightMouseDown,
            self._handle_global_mouse_down,
        )
        self._build_panel()
        return self

    def _build_panel(self) -> None:
        frame = AppKit.NSMakeRect(0, 0, SWITCH_WIDTH, SWITCH_HEIGHT)
        style = AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel
        self.panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            frame,
            style,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        self.panel.setLevel_(AppKit.NSFloatingWindowLevel)
        self.panel.setOpaque_(False)
        self.panel.setHasShadow_(True)
        self.panel.setHidesOnDeactivate_(False)
        self.panel.setIgnoresMouseEvents_(False)
        self.panel.setBackgroundColor_(AppKit.NSColor.clearColor())
        self.panel.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
        )

        content = AppKit.NSVisualEffectView.alloc().initWithFrame_(frame)
        content.setMaterial_(AppKit.NSVisualEffectMaterialHUDWindow)
        content.setBlendingMode_(AppKit.NSVisualEffectBlendingModeBehindWindow)
        content.setState_(AppKit.NSVisualEffectStateActive)
        content.setWantsLayer_(True)
        content.layer().setCornerRadius_(SWITCH_HEIGHT / 2)
        content.layer().setMasksToBounds_(True)

        control = AppKit.NSSegmentedControl.alloc().initWithFrame_(
            AppKit.NSMakeRect(
                SWITCH_PADDING_X,
                SWITCH_PADDING_Y,
                SWITCH_WIDTH - SWITCH_PADDING_X * 2,
                SWITCH_HEIGHT - SWITCH_PADDING_Y * 2,
            )
        )
        control.setSegmentCount_(3)
        control.setTrackingMode_(AppKit.NSSegmentSwitchTrackingSelectOne)
        for index, tooltip in enumerate(TRIGGER_TOOLTIPS):
            self._configure_segment(control, index, tooltip)
            control.setWidth_forSegment_(SWITCH_SEGMENT_WIDTH, index)
        control.setSelectedSegment_(self.speaker.mode_index())
        control.setTarget_(self)
        control.setAction_("modeChanged:")
        content.addSubview_(control)

        self.control = control
        self.panel.setContentView_(content)
        self.panel.orderOut_(None)

    def _configure_segment(self, control, index: int, tooltip: str) -> None:
        symbol_name = TRIGGER_ICON_NAMES[index]
        if hasattr(AppKit.NSImage, "imageWithSystemSymbolName_accessibilityDescription_"):
            image = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol_name, tooltip)
            if image is not None:
                image.setTemplate_(True)
                image.setSize_(AppKit.NSMakeSize(11, 11))
                control.setImage_forSegment_(image, index)
                control.setToolTip_forSegment_(tooltip, index)
                return

        control.setLabel_forSegment_(TRIGGER_FALLBACK_MARKS[index], index)
        control.setToolTip_forSegment_(tooltip, index)

    def modeChanged_(self, sender):
        self.was_clicked = True
        self.speaker.set_trigger_mode(TRIGGER_MODES[sender.selectedSegment()])
        self.dismiss_current_selection()

    def show_for_selection(self, selection_id: str) -> None:
        if self.panel is None:
            return
        if self.dismissed_selection_id == selection_id:
            return
        if self.current_selection_id == selection_id and self.panel.isVisible():
            return

        self.current_selection_id = selection_id
        self.was_clicked = False
        self.shown_at = time.monotonic()
        mouse = AppKit.NSEvent.mouseLocation()
        frame = self.panel.frame()
        screen = self._screen_at_point(mouse)
        visible = screen.visibleFrame() if screen is not None else AppKit.NSMakeRect(0, 0, 1440, 900)
        x = min(max(mouse.x + 12, visible.origin.x), visible.origin.x + visible.size.width - frame.size.width)
        y = min(max(mouse.y - 8, visible.origin.y), visible.origin.y + visible.size.height - frame.size.height)
        self.panel.setFrameOrigin_(AppKit.NSMakePoint(x, y))
        self.panel.orderFrontRegardless()

    def hide(self) -> None:
        if self.panel is not None:
            self.panel.orderOut_(None)

    def dismiss_current_selection(self) -> None:
        if self.current_selection_id is not None:
            self.dismissed_selection_id = self.current_selection_id
        self.hide()

    def auto_hide_if_idle(self) -> None:
        if SWITCH_AUTO_HIDE_SECONDS <= 0:
            return
        if self.panel is None or not self.panel.isVisible():
            return
        if self.was_clicked or self.current_selection_id is None:
            return
        if time.monotonic() - self.shown_at >= SWITCH_AUTO_HIDE_SECONDS:
            self.dismiss_current_selection()

    def clear_selection(self) -> None:
        self.current_selection_id = None
        self.dismissed_selection_id = None
        self.shown_at = 0.0
        self.was_clicked = False
        self.hide()

    def sync(self) -> None:
        if self.control is not None:
            self.control.setSelectedSegment_(self.speaker.mode_index())

    def _handle_global_mouse_down(self, event) -> None:
        if self.panel is None or not self.panel.isVisible():
            return

        if not self._point_is_inside_panel(event.locationInWindow()):
            self.dismiss_current_selection()

    def handle_local_mouse_down(self, event) -> None:
        if self.panel is None or not self.panel.isVisible():
            return
        if event.window() == self.panel:
            return
        self.dismiss_current_selection()

    def _screen_at_point(self, point):
        for screen in AppKit.NSScreen.screens():
            sf = screen.frame()
            if sf.origin.x <= point.x <= sf.origin.x + sf.size.width and sf.origin.y <= point.y <= sf.origin.y + sf.size.height:
                return screen
        return AppKit.NSScreen.mainScreen()

    def _point_is_inside_panel(self, point) -> bool:
        frame = self.panel.frame()
        return (
            frame.origin.x <= point.x <= frame.origin.x + frame.size.width
            and frame.origin.y <= point.y <= frame.origin.y + frame.size.height
        )


class CursorRegionOverlay:
    def __init__(self) -> None:
        self.panel = None
        self.mouse_monitor = AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            AppKit.NSEventMaskMouseMoved | AppKit.NSEventMaskLeftMouseDragged | AppKit.NSEventMaskRightMouseDragged,
            self._handle_mouse_move,
        )
        self._build_panel()

    def _build_panel(self) -> None:
        frame = AppKit.NSMakeRect(0, 0, CURSOR_REGION_DIAMETER, CURSOR_REGION_DIAMETER)
        style = AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel
        self.panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            frame,
            style,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        self.panel.setLevel_(AppKit.NSFloatingWindowLevel)
        self.panel.setOpaque_(False)
        self.panel.setHasShadow_(False)
        self.panel.setHidesOnDeactivate_(False)
        self.panel.setIgnoresMouseEvents_(True)
        self.panel.setBackgroundColor_(AppKit.NSColor.clearColor())
        self.panel.setSharingType_(AppKit.NSWindowSharingNone)
        self.panel.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
        )

        content = AppKit.NSView.alloc().initWithFrame_(frame)
        content.setWantsLayer_(True)
        layer = content.layer()
        layer.setCornerRadius_(CURSOR_REGION_DIAMETER / 2)
        layer.setMasksToBounds_(True)
        layer.setBorderWidth_(0)

        self.panel.setContentView_(content)
        self.set_ready_(False)
        self.panel.orderOut_(None)

    def update(self) -> None:
        if self.panel is None:
            return
        mouse = AppKit.NSEvent.mouseLocation()
        origin = AppKit.NSMakePoint(
            mouse.x - CURSOR_REGION_DIAMETER / 2,
            mouse.y - CURSOR_REGION_DIAMETER / 2,
        )
        self.panel.setFrameOrigin_(origin)
        self.panel.orderFrontRegardless()

    def _handle_mouse_move(self, _event) -> None:
        if self.is_visible():
            self.update()

    def hide(self) -> None:
        if self.panel is not None:
            self.panel.orderOut_(None)

    def is_visible(self) -> bool:
        return bool(self.panel is not None and self.panel.isVisible())

    def set_ready_(self, ready: bool) -> None:
        if self.panel is None or self.panel.contentView() is None:
            return
        color = (
            AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.28, 0.88, 0.62, 1)
            if ready
            else AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.20, 0.68, 0.92, 1)
        )
        layer = self.panel.contentView().layer()
        layer.setBorderWidth_(0)
        layer.setBackgroundColor_(color.colorWithAlphaComponent_(0.09 if not ready else 0.14).CGColor())
        layer.setBorderColor_(AppKit.NSColor.clearColor().CGColor())

    def window_number(self) -> int:
        return int(self.panel.windowNumber()) if self.panel is not None else Quartz.kCGNullWindowID


class HoverSpeaker:
    def __init__(self) -> None:
        self.last_spoken: SpokenWord | None = None
        self.speech_process: subprocess.Popen | None = None
        self.speech_text: str | None = None
        self.selection_text: str | None = None
        self.selection_id: str | None = None
        self.selection_next_speak_at = 0.0
        self.last_selection_copy_at = 0.0
        self.hover_candidate: tuple[str, str, float, object] | None = None
        self.accessibility_enabled = False
        self.warned_about_screenshot = False
        self.trigger_mode = TRIGGER_MODE if TRIGGER_MODE in TRIGGER_MODES else TRIGGER_BOTH
        self.app = None
        self.trigger_switch = None
        self.cursor_overlay = None
        self.available_voices: set[str] | None = None

    def run(self) -> None:
        self._setup_app()
        self.accessibility_enabled = self._request_accessibility_access()
        if not self.accessibility_enabled and not OCR_ENABLED:
            print(
                "HoverSpeak needs Accessibility permission. Enable it in "
                "System Settings > Privacy & Security > Accessibility, then run again."
            )
            sys.exit(1)

        print("HoverSpeak is running. Move the cursor over text. Press Ctrl+C to stop.")
        print("Tip: set HOVERSPEAK_VOICE=Daniel or HOVERSPEAK_CJK_RATE=150 before launching.")
        print(f"Trigger mode: {self.trigger_mode}. Highlight text to show the three-stage switch.")
        if SELECTION_ENABLED:
            print("Selection loop is on. Highlight text to repeat it; clear the highlight to return to hover mode.")
            if self._menu_copy_fallback_enabled():
                print("Menu copy fallback is on. HoverSpeak may use the app's Copy menu item without sending keys.")
            if PROTECTED_KEYBOARD_COPY:
                print("Protected copy fallback is on for apps that do not expose selection text.")
            if self._keyboard_copy_fallback_enabled():
                print("Unsafe keyboard copy fallback is on. HoverSpeak may briefly send Cmd+C.")
            elif not self._menu_copy_fallback_enabled():
                print("Keyboard copy fallback is off. HoverSpeak will not send Cmd+C.")
        if not self.accessibility_enabled:
            print("Accessibility is not enabled, so HoverSpeak will use OCR-only mode.")
        if OCR_ENABLED:
            print("OCR fallback is on. If prompted, allow Screen Recording for this terminal app.")

        signal.signal(signal.SIGINT, self._stop)
        signal.signal(signal.SIGTERM, self._stop)

        has_selection = False
        next_poll_at = 0.0
        while True:
            self._drain_app_events()
            if self.trigger_switch is not None:
                self.trigger_switch.auto_hide_if_idle()
            self._update_cursor_overlay(has_selection)

            now = time.monotonic()
            if now >= next_poll_at:
                has_selection = self._handle_selected_text()
                self._update_cursor_overlay(has_selection)
                if not has_selection and self.trigger_mode == TRIGGER_BOTH:
                    self._speak_word_under_cursor()
                next_poll_at = now + POLL_INTERVAL

            time.sleep(UI_TICK_INTERVAL)

    def _setup_app(self) -> None:
        self.app = AppKit.NSApplication.sharedApplication()
        self.app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        self.trigger_switch = TriggerSwitchController.alloc().initWithSpeaker_(self)
        self.cursor_overlay = CursorRegionOverlay()

    def _drain_app_events(self) -> None:
        if self.app is None:
            return
        while True:
            event = self.app.nextEventMatchingMask_untilDate_inMode_dequeue_(
                AppKit.NSEventMaskAny,
                AppKit.NSDate.dateWithTimeIntervalSinceNow_(0),
                AppKit.NSDefaultRunLoopMode,
                True,
            )
            if event is None:
                break
            if self.trigger_switch is not None and event.type() in (
                AppKit.NSEventTypeLeftMouseDown,
                AppKit.NSEventTypeRightMouseDown,
            ):
                self.trigger_switch.handle_local_mouse_down(event)
            self.app.sendEvent_(event)
            self.app.updateWindows()

    def mode_index(self) -> int:
        return TRIGGER_MODES.index(self.trigger_mode)

    def set_trigger_mode(self, mode: str) -> None:
        if mode not in TRIGGER_MODES or mode == self.trigger_mode:
            return
        self.trigger_mode = mode
        if mode == TRIGGER_OFF:
            self._stop_speech()
        self.selection_next_speak_at = 0.0
        self.hover_candidate = None
        if self.trigger_switch is not None:
            self.trigger_switch.sync()
        if mode != TRIGGER_BOTH and self.cursor_overlay is not None:
            self.cursor_overlay.hide()
        print(f"Trigger mode changed: {mode}")

    def _update_cursor_overlay(self, has_selection: bool) -> None:
        if self.cursor_overlay is None:
            return
        if self.trigger_mode == TRIGGER_BOTH and not has_selection:
            self.cursor_overlay.update()
            if not self._hover_candidate_is_ready_at_current_mouse():
                self.cursor_overlay.set_ready_(False)
        else:
            self.cursor_overlay.hide()

    def _hover_candidate_is_ready_at_current_mouse(self) -> bool:
        if self.hover_candidate is None:
            return False
        _word, _description, candidate_started_at, candidate_point = self.hover_candidate
        current_point = self._mouse_point_for_accessibility()
        return (
            time.monotonic() - candidate_started_at >= HOVER_DWELL
            and self._distance_between_points(candidate_point, current_point) <= HOVER_MOVE_TOLERANCE
        )

    def _request_accessibility_access(self) -> bool:
        options = {AS.kAXTrustedCheckOptionPrompt: True}
        return bool(AS.AXIsProcessTrustedWithOptions(options))

    def _speak_word_under_cursor(self) -> None:
        if self.trigger_mode != TRIGGER_BOTH:
            return
        if self._is_speaking():
            return

        point = self._mouse_point_for_accessibility()
        word, description = None, "accessibility:disabled"
        if self.accessibility_enabled:
            word, description = self._accessibility_text_inside_cursor_region(point)
        if not word and OCR_ENABLED:
            word = self._ocr_word_under_cursor(point)
            description = f"ocr-region:{int(point.x)}:{int(point.y)}"

        if not word or not self._is_speakable(word):
            self.hover_candidate = None
            if self.cursor_overlay is not None:
                self.cursor_overlay.set_ready_(False)
            return

        if not self._hover_candidate_has_dwelled(word, description, point):
            if self.cursor_overlay is not None:
                self.cursor_overlay.set_ready_(False)
            return

        if self.cursor_overlay is not None:
            self.cursor_overlay.set_ready_(True)

        if self._should_skip(word, description):
            return

        self._speak(word)
        self.last_spoken = SpokenWord(word, description, time.monotonic())
        print(f"Spoke: {word}")

    def _hover_candidate_has_dwelled(self, word: str, description: str, point) -> bool:
        now = time.monotonic()
        if self.hover_candidate is None:
            self.hover_candidate = (word, description, now, point)
            return False

        candidate_word, candidate_description, candidate_started_at, candidate_point = self.hover_candidate
        same_target = (
            candidate_word.casefold() == word.casefold()
            and candidate_description == description
            and self._distance_between_points(candidate_point, point) <= HOVER_MOVE_TOLERANCE
        )
        if not same_target:
            self.hover_candidate = (word, description, now, point)
            return False

        return now - candidate_started_at >= HOVER_DWELL

    def _handle_selected_text(self) -> bool:
        if not SELECTION_ENABLED or not self.accessibility_enabled:
            self._clear_selection_state()
            return False

        selected = self._selected_text_from_frontmost_app()
        if not selected:
            if self.selection_text is not None:
                self._clear_selection_state()
            if self.trigger_switch is not None:
                self.trigger_switch.clear_selection()
            return False

        selection_id = self._selection_identity(selected)

        if selected != self.selection_text:
            self._stop_speech()
            self.hover_candidate = None
            self.selection_text = selected
            self.selection_id = selection_id
            self.selection_next_speak_at = 0.0
            print(f"Selected: {selected[:80]}")

        if self.trigger_switch is not None:
            self.trigger_switch.show_for_selection(selection_id)

        if self.trigger_mode == TRIGGER_OFF:
            self._stop_speech()
            return True

        if self.trigger_mode not in (TRIGGER_SELECTION, TRIGGER_BOTH):
            return True

        if self._is_speaking():
            return True

        now = time.monotonic()
        if now >= self.selection_next_speak_at:
            self._speak(selected)
            self.selection_next_speak_at = now + SELECTION_REPEAT_PAUSE
            print(f"Loop: {selected[:80]}")

        return True

    def _clear_selection_state(self) -> None:
        if self.selection_text is not None and self.speech_text == self.selection_text:
            self._stop_speech()
        self.selection_text = None
        self.selection_id = None
        self.selection_next_speak_at = 0.0
        self.hover_candidate = None

    def _selection_identity(self, selected: str) -> str:
        app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
        bundle = app.bundleIdentifier() if app is not None else "unknown"
        return f"{bundle}:{selected}"

    def _selected_text_from_frontmost_app(self) -> str | None:
        app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return None

        app_element = AS.AXUIElementCreateApplication(app.processIdentifier())
        roots = [
            self._selected_text_root_under_mouse(),
            self._attribute(app_element, "AXFocusedUIElement"),
            self._attribute(app_element, "AXFocusedWindow"),
            app_element,
        ]

        for root in roots:
            if root is None:
                continue
            selected = self._selected_text_in_tree(root)
            if selected:
                return selected

        if self._should_use_copy_fallback(app_element):
            return self._selected_text_by_copy()

        if self._menu_copy_fallback_enabled():
            selected = self._selected_text_via_menu()
            if selected:
                return selected

        if self._protected_keyboard_copy_fallback_enabled(app_element):
            return self._selected_text_by_protected_copy()

        return None

    def _selected_text_root_under_mouse(self):
        point = self._mouse_point_for_accessibility()
        system = AS.AXUIElementCreateSystemWide()
        result, element = AS.AXUIElementCopyElementAtPosition(system, point.x, point.y, None)
        if result != AS.kAXErrorSuccess or element is None:
            return None
        return element

    def _should_use_copy_fallback(self, app_element) -> bool:
        if not self._keyboard_copy_fallback_enabled():
            return False
        app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
        bundle = app.bundleIdentifier() if app is not None else ""
        if bundle in SELECTION_COPY_BLOCKED_BUNDLES:
            return False
        return not self._focused_element_is_editable(app_element)

    def _menu_copy_fallback_enabled(self) -> bool:
        return SELECTION_COPY_MODE in SAFE_COPY_MODES

    def _keyboard_copy_fallback_enabled(self) -> bool:
        return UNSAFE_KEYBOARD_COPY and SELECTION_COPY_MODE in UNSAFE_COPY_MODES

    def _protected_keyboard_copy_fallback_enabled(self, app_element) -> bool:
        if not PROTECTED_KEYBOARD_COPY or SELECTION_COPY_MODE == "off":
            return False
        app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
        bundle = app.bundleIdentifier() if app is not None else ""
        if bundle in PROTECTED_KEYBOARD_COPY_BLOCKED_BUNDLES:
            return False
        return not self._focused_element_is_editable(app_element)

    def _focused_element_is_editable(self, app_element) -> bool:
        focused = self._attribute(app_element, "AXFocusedUIElement")
        if focused is None:
            return False

        current = focused
        seen = set()
        for _ in range(6):
            if current is None:
                break
            key = repr(current)
            if key in seen:
                break
            seen.add(key)

            if self._element_is_editable(current):
                return True
            current = self._attribute(current, AS.kAXParentAttribute)

        return False

    def _element_is_editable(self, element) -> bool:
        role = self._string_attribute(element, AS.kAXRoleAttribute) or ""
        subrole = self._string_attribute(element, "AXSubrole") or ""
        if role in {"AXTextArea", "AXTextField", "AXComboBox", "AXSearchField"}:
            return True
        if subrole in {"AXTextAttachment", "AXSecureTextField"}:
            return True

        editable = self._attribute(element, "AXEditable")
        if editable is not None:
            return bool(editable)

        writable = self._attribute_names(element)
        return "AXSelectedTextRange" in writable and "AXValue" in writable

    def _selected_text_in_tree(self, root) -> str | None:
        queue = [(root, 0)]
        seen = set()

        idx = 0
        while idx < len(queue):
            element, depth = queue[idx]
            idx += 1
            key = repr(element)
            if key in seen:
                continue
            seen.add(key)

            selected = self._clean_selected_text(self._string_attribute(element, AS.kAXSelectedTextAttribute))
            if selected:
                return selected

            selected = self._selected_text_from_range(element)
            if selected:
                return selected

            if depth >= 8:
                continue

            for child_attr in CHILD_ATTRIBUTES:
                children = self._attribute(element, child_attr)
                if isinstance(children, (list, tuple)):
                    queue.extend((child, depth + 1) for child in children)

        return None

    def _selected_text_from_range(self, element) -> str | None:
        text = self._string_attribute(element, AS.kAXValueAttribute)
        if not text:
            return None

        raw_range = self._attribute(element, AS.kAXSelectedTextRangeAttribute)
        if raw_range is None:
            return None

        ok, selected_range = AS.AXValueGetValue(raw_range, AS.kAXValueCFRangeType, None)
        if not ok or selected_range is None:
            return None

        location, length = selected_range
        if length <= 0:
            return None

        return self._clean_selected_text(text[location : location + length])

    def _clean_selected_text(self, text: str | None) -> str | None:
        if not text:
            return None
        cleaned = re.sub(r"\s+", " ", text).strip()
        if not cleaned or not any(ch.isalpha() for ch in cleaned):
            return None
        return cleaned[:SELECTION_MAX_CHARS]

    def _selected_text_by_copy(self) -> str | None:
        now = time.monotonic()
        if now - self.last_selection_copy_at < SELECTION_COPY_INTERVAL:
            return self.selection_text
        self.last_selection_copy_at = now

        pasteboard = AppKit.NSPasteboard.generalPasteboard()
        old_change_count = pasteboard.changeCount()
        snapshot = self._snapshot_pasteboard(pasteboard)

        self._send_copy_shortcut()
        time.sleep(0.08)

        new_change_count = pasteboard.changeCount()
        copied = pasteboard.stringForType_(AppKit.NSPasteboardTypeString)

        self._restore_pasteboard(pasteboard, snapshot)

        if new_change_count == old_change_count:
            return None

        return self._clean_selected_text(copied)

    def _selected_text_by_protected_copy(self) -> str | None:
        now = time.monotonic()
        if now - self.last_selection_copy_at < SELECTION_COPY_INTERVAL:
            return self.selection_text
        self.last_selection_copy_at = now

        pasteboard = AppKit.NSPasteboard.generalPasteboard()
        old_change_count = pasteboard.changeCount()
        snapshot = self._snapshot_pasteboard(pasteboard)

        self._send_protected_copy_shortcut()
        time.sleep(0.08)

        new_change_count = pasteboard.changeCount()
        copied = pasteboard.stringForType_(AppKit.NSPasteboardTypeString)

        self._restore_pasteboard(pasteboard, snapshot)

        if new_change_count == old_change_count:
            return None

        return self._clean_selected_text(copied)

    def _selected_text_via_menu(self) -> str | None:
        now = time.monotonic()
        if now - self.last_selection_copy_at < SELECTION_COPY_INTERVAL:
            return self.selection_text
        self.last_selection_copy_at = now

        pasteboard = AppKit.NSPasteboard.generalPasteboard()
        old_change_count = pasteboard.changeCount()
        snapshot = self._snapshot_pasteboard(pasteboard)

        app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None or not self._press_menu_copy(app.processIdentifier()):
            self.last_selection_copy_at = 0.0
            self._restore_pasteboard(pasteboard, snapshot)
            return None

        time.sleep(0.1)

        new_change_count = pasteboard.changeCount()
        copied = pasteboard.stringForType_(AppKit.NSPasteboardTypeString)

        self._restore_pasteboard(pasteboard, snapshot)

        if new_change_count == old_change_count:
            self.last_selection_copy_at = 0.0
            return None

        return self._clean_selected_text(copied)

    def _press_menu_copy(self, pid: int) -> bool:
        app_element = AS.AXUIElementCreateApplication(pid)
        menu_bar = self._attribute(app_element, "AXMenuBar")
        if menu_bar is None:
            return False
        return self._walk_menu_for_copy(menu_bar, 0)

    def _walk_menu_for_copy(self, element, depth: int) -> bool:
        if depth > 10:
            return False

        role = self._string_attribute(element, "AXRole") or ""

        if role == "AXMenuItem":
            title = (self._string_attribute(element, "AXTitle") or "").strip()
            vkey = self._attribute(element, "AXMenuItemCmdVirtualKey")
            modifiers = self._attribute(element, "AXMenuItemCmdModifiers")
            try:
                vkey = int(vkey)
            except (TypeError, ValueError):
                vkey = None
            try:
                modifiers = int(modifiers)
            except (TypeError, ValueError):
                modifiers = None

            is_copy_title = title.casefold() in {"copy", "复制", "拷贝"}
            is_cmd_c = vkey == 8 and (modifiers is None or modifiers in {0, 256})
            if is_copy_title or is_cmd_c:
                return AS.AXUIElementPerformAction(element, AS.kAXPressAction) == AS.kAXErrorSuccess

        children = self._attribute(element, "AXChildren")
        if isinstance(children, (list, tuple)):
            for child in children:
                if self._walk_menu_for_copy(child, depth + 1):
                    return True

        return False

    def _snapshot_pasteboard(self, pasteboard):
        snapshot = []
        for item in pasteboard.pasteboardItems() or []:
            stored_types = []
            for item_type in item.types() or []:
                data = item.dataForType_(item_type)
                if data is not None:
                    stored_types.append((item_type, data))
            if stored_types:
                snapshot.append(stored_types)
        return snapshot

    def _restore_pasteboard(self, pasteboard, snapshot) -> None:
        pasteboard.clearContents()
        restored_items = []
        for stored_types in snapshot:
            item = AppKit.NSPasteboardItem.alloc().init()
            for item_type, data in stored_types:
                item.setData_forType_(data, item_type)
            restored_items.append(item)
        if restored_items:
            pasteboard.writeObjects_(restored_items)

    def _send_copy_shortcut(self) -> None:
        if not self._keyboard_copy_fallback_enabled():
            return

        source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
        command_down = Quartz.CGEventCreateKeyboardEvent(source, 55, True)
        c_down = Quartz.CGEventCreateKeyboardEvent(source, 8, True)
        c_up = Quartz.CGEventCreateKeyboardEvent(source, 8, False)
        command_up = Quartz.CGEventCreateKeyboardEvent(source, 55, False)

        Quartz.CGEventSetFlags(c_down, Quartz.kCGEventFlagMaskCommand)
        Quartz.CGEventSetFlags(c_up, Quartz.kCGEventFlagMaskCommand)

        for event in (command_down, c_down, c_up, command_up):
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

    def _send_protected_copy_shortcut(self) -> None:
        source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
        c_down = Quartz.CGEventCreateKeyboardEvent(source, 8, True)
        c_up = Quartz.CGEventCreateKeyboardEvent(source, 8, False)

        Quartz.CGEventSetFlags(c_down, Quartz.kCGEventFlagMaskCommand)
        Quartz.CGEventSetFlags(c_up, Quartz.kCGEventFlagMaskCommand)

        for event in (c_down, c_up):
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


    def _accessibility_text_inside_cursor_region(self, point) -> tuple[str | None, str]:
        system = AS.AXUIElementCreateSystemWide()
        result, element = AS.AXUIElementCopyElementAtPosition(system, point.x, point.y, None)
        if result != AS.kAXErrorSuccess or element is None:
            return None, "accessibility:none"

        for candidate in self._candidate_elements_at_point(point, element):
            text = self._string_attribute(candidate, AS.kAXValueAttribute)
            text = text or self._string_attribute(candidate, AS.kAXTitleAttribute)
            text = text or self._string_attribute(candidate, AS.kAXDescriptionAttribute)
            if not text:
                continue

            region_text = self._text_inside_region_by_bounds(point, candidate, text)
            if DEBUG:
                self._debug_element(candidate, point, region_text)
            if region_text:
                return region_text, self._describe(candidate)

        return None, self._describe(element)

    def _ocr_word_under_cursor(self, point) -> str | None:
        image = self._screenshot_around(point)
        if image is None:
            return None

        lines: list[tuple[str, object]] = []

        def completion(request, _error):
            for observation in request.results() or []:
                candidates = observation.topCandidates_(1)
                if candidates:
                    lines.append((str(candidates[0].string()), observation.boundingBox()))

        request = Vision.VNRecognizeTextRequest.alloc().initWithCompletionHandler_(completion)
        level = (
            Vision.VNRequestTextRecognitionLevelAccurate
            if OCR_ACCURATE
            else Vision.VNRequestTextRecognitionLevelFast
        )
        request.setRecognitionLevel_(level)
        request.setUsesLanguageCorrection_(True)
        if hasattr(request, "setMinimumTextHeight_"):
            request.setMinimumTextHeight_(OCR_MIN_TEXT_HEIGHT)
        if OCR_LANGUAGES and hasattr(request, "setRecognitionLanguages_"):
            request.setRecognitionLanguages_(OCR_LANGUAGES)

        handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(image, {})
        ok, error = handler.performRequests_error_([request], None)
        if not ok or error is not None:
            return None

        crop_origin = Quartz.CGPoint(point.x - OCR_WIDTH / 2, point.y - OCR_HEIGHT / 2)
        return self._word_near_point(lines, point, crop_origin)

    def _screenshot_around(self, point):
        crop = Quartz.CGRectMake(
            point.x - OCR_WIDTH / 2,
            point.y - OCR_HEIGHT / 2,
            OCR_WIDTH,
            OCR_HEIGHT,
        )
        capture_options = Quartz.kCGWindowListOptionOnScreenOnly
        window_id = Quartz.kCGNullWindowID
        if self.cursor_overlay is not None and self.cursor_overlay.is_visible():
            capture_options = Quartz.kCGWindowListOptionOnScreenBelowWindow
            window_id = self.cursor_overlay.window_number()

        image = Quartz.CGWindowListCreateImage(
            crop,
            capture_options,
            window_id,
            Quartz.kCGWindowImageDefault,
        )

        if image is None and not self.warned_about_screenshot:
            print(
                "OCR could not capture the screen. Enable Screen Recording for this terminal app "
                "in System Settings > Privacy & Security > Screen & System Audio Recording."
            )
            self.warned_about_screenshot = True
        return image


    def _word_near_point(self, lines: list[tuple[str, object]], point, crop_origin) -> str | None:
        best_word = None
        best_score = float("inf")
        best_line = None
        best_line_score = float("inf")

        for text, box in lines:
            text = text.strip()
            if not text:
                continue

            line_rect = self._vision_box_to_screen_rect(box, crop_origin)
            if not self._line_is_vertically_near_cursor(line_rect, point):
                continue

            line_text = self._line_text_inside_region(text, line_rect, point)
            if line_text:
                score = self._distance_between_points(self._rect_center(line_rect), point)
                if score < best_line_score:
                    best_line_score = score
                    best_line = line_text

            tokens = list(TOKEN_RE.finditer(text))
            if not tokens:
                continue

            for match in tokens:
                token = match.group(0).strip()
                if not token:
                    continue
                token_rect = self._estimate_token_rect(line_rect, text, match.start(), match.end())
                if not self._rect_intersects_cursor_region(token_rect, point):
                    continue
                score = self._distance_to_rect(token_rect, point)
                if self._rect_contains(token_rect, point):
                    score = 0
                if score < best_score:
                    best_score = score
                    best_word = token.strip()

        if best_line:
            return best_line
        return best_word

    def _line_is_vertically_near_cursor(self, line_rect, point) -> bool:
        line_center_y = line_rect.origin.y + line_rect.size.height / 2
        tolerance = max(8, min(CURSOR_REGION_DIAMETER * 0.22, line_rect.size.height * 1.45))
        return abs(line_center_y - point.y) <= tolerance

    def _line_text_inside_region(self, text: str, line_rect, point) -> str | None:
        cleaned = re.sub(r"\s+", " ", text).strip()
        if not cleaned:
            return None

        segments = []
        segment_start = None
        segment_chars = []
        for index, char in enumerate(text):
            inside = self._char_rect_is_inside_region(line_rect, text, index, point)
            if inside:
                if segment_start is None:
                    segment_start = index
                segment_chars.append(char)
                continue

            if segment_start is not None:
                segments.append((segment_start, index, "".join(segment_chars)))
                segment_start = None
                segment_chars = []

        if segment_start is not None:
            segments.append((segment_start, len(text), "".join(segment_chars)))

        candidates = []
        for start, end, segment in segments:
            segment = self._trim_partial_latin_edges(text, start, end, segment)
            segment = re.sub(r"\s+", " ", segment).strip()
            segment = segment.strip(" ,.;:!?，。！？；：、")
            if self._is_speakable_line(segment):
                candidates.append(segment)

        if not candidates:
            return None

        return max(candidates, key=len)

    def _char_rect_is_inside_region(self, line_rect, text: str, index: int, point) -> bool:
        length = max(len(text), 1)
        char_width = line_rect.size.width / length
        char_rect = Quartz.CGRectMake(
            line_rect.origin.x + char_width * index,
            line_rect.origin.y,
            char_width,
            line_rect.size.height,
        )
        return self._rect_intersects_cursor_region(char_rect, point)

    def _rect_center_is_inside_region(self, rect, point) -> bool:
        center = self._rect_center(rect)
        radius = CURSOR_REGION_DIAMETER / 2
        return self._distance_between_points(center, point) <= radius

    def _rect_intersects_cursor_region(self, rect, point) -> bool:
        radius = CURSOR_REGION_DIAMETER / 2
        closest_x = min(max(point.x, rect.origin.x), rect.origin.x + rect.size.width)
        closest_y = min(max(point.y, rect.origin.y), rect.origin.y + rect.size.height)
        closest = Quartz.CGPoint(closest_x, closest_y)
        return self._distance_between_points(closest, point) <= radius

    def _trim_partial_latin_edges(self, text: str, start: int, end: int, segment: str) -> str:
        if not segment:
            return segment

        trimmed = segment
        if start > 0 and self._is_latin_word_char(text[start - 1]):
            trimmed = re.sub(rf"^[^\W_{CJK_RANGES}]+(?:['-]?)", "", trimmed, flags=re.UNICODE)
        if end < len(text) and self._is_latin_word_char(text[end]):
            trimmed = re.sub(rf"(?:['-]?)[^\W_{CJK_RANGES}]+$", "", trimmed, flags=re.UNICODE)
        return trimmed

    def _is_latin_word_char(self, char: str) -> bool:
        return bool(char and re.match(rf"[^\W_{CJK_RANGES}'-]", char, re.UNICODE))

    def _is_speakable_line(self, text: str) -> bool:
        return 2 <= len(text) <= OCR_LINE_MAX_CHARS and any(ch.isalpha() for ch in text)

    def _vision_box_to_screen_rect(self, box, crop_origin):
        x = crop_origin.x + box.origin.x * OCR_WIDTH
        y = crop_origin.y + (1 - box.origin.y - box.size.height) * OCR_HEIGHT + OCR_Y_OFFSET
        width = box.size.width * OCR_WIDTH
        height = box.size.height * OCR_HEIGHT
        return Quartz.CGRectMake(x, y, width, height)

    def _estimate_token_rect(self, line_rect, text: str, start: int, end: int):
        length = max(len(text), 1)
        x = line_rect.origin.x + line_rect.size.width * (start / length)
        width = line_rect.size.width * max((end - start) / length, 0.02)
        return Quartz.CGRectMake(x, line_rect.origin.y, width, line_rect.size.height)

    def _mouse_point_for_accessibility(self):
        mouse = AppKit.NSEvent.mouseLocation()
        max_y = max(screen.frame().origin.y + screen.frame().size.height for screen in AppKit.NSScreen.screens())
        return Quartz.CGPoint(mouse.x, max_y - mouse.y)

    def _candidate_elements_at_point(self, point, element):
        seen = set()
        candidates = []

        def add(candidate, depth: int) -> None:
            key = repr(candidate)
            if key in seen:
                return
            seen.add(key)

            frame = self._frame_for_element(candidate)
            if frame is not None and not self._rect_contains(frame, point):
                return

            area = frame.size.width * frame.size.height if frame is not None else float("inf")
            candidates.append((area, depth, candidate))

            if depth >= 6:
                return

            for child_attr in CHILD_ATTRIBUTES:
                children = self._attribute(candidate, child_attr)
                if isinstance(children, (list, tuple)):
                    for child in children:
                        add(child, depth + 1)

        current = element
        for _ in range(4):
            if current is None:
                break
            add(current, 0)
            current = self._attribute(current, AS.kAXParentAttribute)

        candidates.sort(key=lambda item: (item[0], -item[1]))
        return [candidate for _, _, candidate in candidates]

    def _word_at(self, point, element) -> str | None:
        range_value = self._range_for_position(point, element)
        full_text = self._string_attribute(element, AS.kAXValueAttribute)
        if range_value is not None and full_text:
            word = self._word_around(range_value[0], full_text)
            if word:
                return word

        full_text = full_text or self._string_attribute(element, AS.kAXTitleAttribute)
        full_text = full_text or self._string_attribute(element, AS.kAXDescriptionAttribute)
        if full_text:
            word = self._word_from_bounds(point, element, full_text)
            if word:
                return word

        selected = self._string_attribute(element, AS.kAXSelectedTextAttribute)
        if selected and self._is_speakable(selected):
            return selected.strip()

        title = self._string_attribute(element, AS.kAXTitleAttribute)
        if title and self._is_speakable(title) and len(title.strip()) <= 40:
            return title.strip()

        return None

    def _range_for_position(self, point, element) -> tuple[int, int] | None:
        point_value = AS.AXValueCreate(AS.kAXValueCGPointType, point)
        result, raw_range = AS.AXUIElementCopyParameterizedAttributeValue(
            element,
            "AXRangeForPosition",
            point_value,
            None,
        )
        if result != AS.kAXErrorSuccess or raw_range is None:
            return None

        ok, value = AS.AXValueGetValue(raw_range, AS.kAXValueCFRangeType, None)
        if not ok:
            return None

        if isinstance(value, tuple):
            return int(value[0]), int(value[1])

        location = getattr(value, "location", None)
        length = getattr(value, "length", None)
        if location is None or length is None:
            return None
        return int(location), int(length)

    def _word_from_bounds(self, point, element, text: str) -> str | None:
        best_word = None
        best_distance = float("inf")

        for start, end, candidate in self._token_spans(text):
            if not self._is_speakable(candidate):
                continue

            bounds = self._bounds_for_range(element, start, end - start)
            if bounds is None:
                continue

            if self._rect_contains(bounds, point):
                return candidate

            distance = self._distance_to_rect(bounds, point)
            if distance < best_distance and distance < 18:
                best_distance = distance
                best_word = candidate

        return best_word

    def _text_inside_region_by_bounds(self, point, element, text: str) -> str | None:
        pieces = []
        for start, end, candidate in self._region_text_spans(text):
            candidate = candidate.strip()
            if not candidate:
                continue

            bounds = self._bounds_for_range(element, start, end - start)
            if bounds is None:
                continue
            if not self._rect_intersects_cursor_region(bounds, point):
                continue

            center = self._rect_center(bounds)
            pieces.append((center.y, center.x, start, candidate, self._is_cjk(candidate)))

        if not pieces:
            return None

        pieces.sort(key=lambda item: (round(item[0] / 8), item[1], item[2]))
        text_inside = self._join_region_pieces(pieces)
        text_inside = re.sub(r"\s+", " ", text_inside).strip()
        text_inside = text_inside.strip(" ,.;:!?，。！？；：、")
        return text_inside if self._is_speakable_line(text_inside) else None

    def _region_text_spans(self, text: str):
        index = 0
        while index < len(text):
            char = text[index]
            if self._is_cjk(char):
                yield index, index + 1, char
                index += 1
                continue

            match = LATIN_WORD_RE.match(text, index)
            if match is not None:
                yield match.start(), match.end(), match.group(0)
                index = match.end()
                continue

            index += 1

    def _join_region_pieces(self, pieces) -> str:
        output = []
        previous_is_cjk = False
        for _y, _x, _start, text, is_cjk in pieces:
            if not output or (previous_is_cjk and is_cjk):
                output.append(text)
            else:
                output.append(" " + text)
            previous_is_cjk = is_cjk
        return "".join(output)

    def _bounds_for_range(self, element, location: int, length: int):
        range_value = AS.AXValueCreate(AS.kAXValueCFRangeType, (location, length))
        result, raw_bounds = AS.AXUIElementCopyParameterizedAttributeValue(
            element,
            AS.kAXBoundsForRangeParameterizedAttribute,
            range_value,
            None,
        )
        if result != AS.kAXErrorSuccess or raw_bounds is None:
            return None

        ok, bounds = AS.AXValueGetValue(raw_bounds, AS.kAXValueCGRectType, None)
        return bounds if ok else None

    def _frame_for_element(self, element):
        position = self._ax_value_attribute(element, AS.kAXPositionAttribute, AS.kAXValueCGPointType)
        size = self._ax_value_attribute(element, AS.kAXSizeAttribute, AS.kAXValueCGSizeType)
        if position is None or size is None:
            return None
        return Quartz.CGRectMake(position.x, position.y, size.width, size.height)

    def _ax_value_attribute(self, element, attribute, value_type):
        result, value = AS.AXUIElementCopyAttributeValue(element, attribute, None)
        if result != AS.kAXErrorSuccess or value is None:
            return None
        ok, converted = AS.AXValueGetValue(value, value_type, None)
        return converted if ok else None

    def _rect_contains(self, rect, point) -> bool:
        return (
            rect.origin.x <= point.x <= rect.origin.x + rect.size.width
            and rect.origin.y <= point.y <= rect.origin.y + rect.size.height
        )

    def _rect_center(self, rect):
        return Quartz.CGPoint(
            rect.origin.x + rect.size.width / 2,
            rect.origin.y + rect.size.height / 2,
        )

    def _distance_to_rect(self, rect, point) -> float:
        left = rect.origin.x
        right = rect.origin.x + rect.size.width
        top = rect.origin.y
        bottom = rect.origin.y + rect.size.height
        dx = max(left - point.x, 0, point.x - right)
        dy = max(top - point.y, 0, point.y - bottom)
        return (dx * dx + dy * dy) ** 0.5

    def _distance_between_points(self, first, second) -> float:
        dx = first.x - second.x
        dy = first.y - second.y
        return (dx * dx + dy * dy) ** 0.5

    def _string_attribute(self, element, attribute) -> str | None:
        value = self._attribute(element, attribute)
        if value is None:
            return None
        return str(value)

    def _attribute(self, element, attribute):
        result, value = AS.AXUIElementCopyAttributeValue(element, attribute, None)
        if result != AS.kAXErrorSuccess or value is None:
            return None
        return value

    def _word_around(self, location: int, text: str) -> str | None:
        if not text:
            return None

        location = max(0, min(location, len(text) - 1))
        if self._is_cjk(text[location]):
            return self._cjk_phrase_around(location, text)

        for match in LATIN_WORD_RE.finditer(text):
            if match.start() <= location <= match.end():
                return match.group(0).strip()
        return None

    def _token_spans(self, text: str):
        for match in TOKEN_RE.finditer(text):
            token = match.group(0).strip()
            if not token:
                continue
            if self._is_cjk(token):
                token = self._cjk_phrase_around(match.start(), text)
                end = min(match.start() + len(token), len(text))
                yield match.start(), end, token
            else:
                yield match.start(), match.end(), token

    def _cjk_phrase_around(self, location: int, text: str) -> str:
        left = location
        while left > 0 and not BOUNDARY_RE.match(text[left - 1]):
            left -= 1

        right = location + 1
        while right < len(text) and not BOUNDARY_RE.match(text[right]):
            right += 1

        phrase_start = max(left, location - max(CJK_PHRASE_CHARS // 2, 1))
        phrase_end = min(right, phrase_start + CJK_PHRASE_CHARS)
        if location >= phrase_end:
            phrase_end = min(right, location + 1)
            phrase_start = max(left, phrase_end - CJK_PHRASE_CHARS)
        return text[phrase_start:phrase_end].strip()

    def _is_cjk(self, text: str) -> bool:
        return bool(text and CJK_RE.search(text))

    def _debug_element(self, element, point, word) -> None:
        role = self._string_attribute(element, AS.kAXRoleAttribute) or "?"
        value = (self._string_attribute(element, AS.kAXValueAttribute) or "").replace("\n", " ")
        title = (self._string_attribute(element, AS.kAXTitleAttribute) or "").replace("\n", " ")
        description = (self._string_attribute(element, AS.kAXDescriptionAttribute) or "").replace("\n", " ")
        frame = self._frame_for_element(element)
        if frame is not None:
            frame_text = f"{int(frame.origin.x)},{int(frame.origin.y)} {int(frame.size.width)}x{int(frame.size.height)}"
        else:
            frame_text = "no-frame"
        print(
            "AX",
            role,
            frame_text,
            f"word={word!r}",
            f"value={value[:80]!r}",
            f"title={title[:80]!r}",
            f"description={description[:80]!r}",
            f"attrs={','.join(self._attribute_names(element)[:12])}",
        )

    def _attribute_names(self, element) -> list[str]:
        result, names = AS.AXUIElementCopyAttributeNames(element, None)
        if result != AS.kAXErrorSuccess or names is None:
            return []
        return [str(name) for name in names]

    def _is_speakable(self, text: str) -> bool:
        text = text.strip()
        return 2 <= len(text) <= 40 and any(ch.isalpha() for ch in text)

    def _should_skip(self, word: str, description: str) -> bool:
        if self.last_spoken is None:
            return False
        return (
            self.last_spoken.text.casefold() == word.casefold()
            and self.last_spoken.element_description == description
            and time.monotonic() - self.last_spoken.spoken_at < REPEAT_COOLDOWN
        )

    def _speak(self, word: str) -> None:
        self._stop_speech()

        speech_text = self._naturalize_text_for_speech(word)
        args = ["/usr/bin/say", "-r", self._rate_for_text(speech_text)]
        voice = self._voice_for_text(speech_text)
        if voice:
            args.extend(["-v", voice])
        args.append(speech_text)
        self.speech_process = subprocess.Popen(args)
        self.speech_text = speech_text

    def _naturalize_text_for_speech(self, text: str) -> str:
        text = re.sub(r"\s+", " ", text).strip()
        text = text.strip(" ,.;:!?，。！？；：、")
        if not text:
            return text

        if not re.search(r"[。！？.!?]$", text):
            text += "。" if self._is_cjk(text) else "."
        return text

    def _rate_for_text(self, text: str) -> str:
        if RATE:
            return RATE
        return DEFAULT_CJK_RATE if self._is_cjk(text) else DEFAULT_LATIN_RATE

    def _voice_for_text(self, text: str) -> str | None:
        if VOICE:
            return VOICE
        if not AUTO_VOICE:
            return None

        voices = self._available_voice_names()
        candidates = CJK_VOICE_CANDIDATES if self._is_cjk(text) else LATIN_VOICE_CANDIDATES
        for candidate in candidates:
            if candidate in voices:
                return candidate
        return None

    def _available_voice_names(self) -> set[str]:
        if self.available_voices is not None:
            return self.available_voices

        voices: set[str] = set()
        try:
            output = subprocess.check_output(
                ["/usr/bin/say", "-v", "?"],
                text=True,
                stderr=subprocess.DEVNULL,
            )
            for line in output.splitlines():
                match = re.match(r"^(.*?)\s+[a-z]{2}(?:_[A-Z0-9]+)?\s+#", line)
                if match:
                    voices.add(match.group(1).strip())
        except Exception:
            voices = set()

        self.available_voices = voices
        return voices

    def _is_speaking(self) -> bool:
        if self.speech_process is None:
            return False
        if self.speech_process.poll() is None:
            return True
        self.speech_process = None
        self.speech_text = None
        if self.selection_text is not None:
            self.selection_next_speak_at = time.monotonic() + SELECTION_REPEAT_PAUSE
        return False

    def _stop_speech(self) -> None:
        if self.speech_process and self.speech_process.poll() is None:
            self.speech_process.terminate()
        self.speech_process = None
        self.speech_text = None

    def _describe(self, element) -> str:
        role = self._string_attribute(element, AS.kAXRoleAttribute) or "unknown"
        title = self._string_attribute(element, AS.kAXTitleAttribute) or ""
        return f"{role}:{title}"

    def _stop(self, *_args) -> None:
        self._stop_speech()
        print("\nHoverSpeak stopped.")
        sys.exit(0)


if __name__ == "__main__":
    HoverSpeaker().run()
