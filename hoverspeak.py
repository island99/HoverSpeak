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
REPEAT_COOLDOWN = float(os.getenv("HOVERSPEAK_COOLDOWN", "1.4"))
VOICE = os.getenv("HOVERSPEAK_VOICE")
RATE = os.getenv("HOVERSPEAK_RATE", "185")
TRIGGER_MODE = os.getenv("HOVERSPEAK_TRIGGER_MODE", "both").lower()
TRIGGER_OFF = "off"
TRIGGER_SELECTION = "selection"
TRIGGER_BOTH = "both"
TRIGGER_MODES = (TRIGGER_OFF, TRIGGER_SELECTION, TRIGGER_BOTH)
TRIGGER_LABELS = ("关", "选", "全")
SELECTION_ENABLED = os.getenv("HOVERSPEAK_SELECTION", "1") != "0"
SELECTION_REPEAT_PAUSE = float(os.getenv("HOVERSPEAK_SELECTION_PAUSE", "0.8"))
SELECTION_MAX_CHARS = int(os.getenv("HOVERSPEAK_SELECTION_MAX_CHARS", "1200"))
SELECTION_COPY_MODE = os.getenv("HOVERSPEAK_SELECTION_COPY", "auto").lower()
SELECTION_COPY_INTERVAL = float(os.getenv("HOVERSPEAK_SELECTION_COPY_INTERVAL", "0.8"))
OCR_ENABLED = os.getenv("HOVERSPEAK_OCR", "1") != "0"
OCR_WIDTH = int(os.getenv("HOVERSPEAK_OCR_WIDTH", "360"))
OCR_HEIGHT = int(os.getenv("HOVERSPEAK_OCR_HEIGHT", "96"))
OCR_ACCURATE = os.getenv("HOVERSPEAK_OCR_ACCURATE", "1") != "0"
OCR_LANGUAGES = [lang.strip() for lang in os.getenv("HOVERSPEAK_OCR_LANGUAGES", "zh-Hans,en-US").split(",") if lang.strip()]
OCR_MIN_TEXT_HEIGHT = float(os.getenv("HOVERSPEAK_OCR_MIN_TEXT_HEIGHT", "0.015"))
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
        self._build_panel()
        return self

    def _build_panel(self) -> None:
        frame = AppKit.NSMakeRect(0, 0, 128, 34)
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
        content.layer().setCornerRadius_(17)
        content.layer().setMasksToBounds_(True)

        control = AppKit.NSSegmentedControl.alloc().initWithFrame_(AppKit.NSMakeRect(8, 5, 112, 24))
        control.setSegmentCount_(3)
        control.setTrackingMode_(AppKit.NSSegmentSwitchTrackingSelectOne)
        for index, label in enumerate(TRIGGER_LABELS):
            control.setLabel_forSegment_(label, index)
            control.setWidth_forSegment_(36, index)
        control.setSelectedSegment_(self.speaker.mode_index())
        control.setTarget_(self)
        control.setAction_("modeChanged:")
        content.addSubview_(control)

        self.control = control
        self.panel.setContentView_(content)
        self.panel.orderOut_(None)

    def modeChanged_(self, sender):
        self.speaker.set_trigger_mode(TRIGGER_MODES[sender.selectedSegment()])

    def show_near_mouse(self) -> None:
        if self.panel is None:
            return

        mouse = AppKit.NSEvent.mouseLocation()
        frame = self.panel.frame()
        screen = AppKit.NSScreen.mainScreen()
        visible = screen.visibleFrame() if screen is not None else AppKit.NSMakeRect(0, 0, 1440, 900)
        x = min(max(mouse.x + 18, visible.origin.x), visible.origin.x + visible.size.width - frame.size.width)
        y = min(max(mouse.y - 14, visible.origin.y), visible.origin.y + visible.size.height - frame.size.height)
        self.panel.setFrameOrigin_(AppKit.NSMakePoint(x, y))
        self.panel.orderFrontRegardless()

    def hide(self) -> None:
        if self.panel is not None:
            self.panel.orderOut_(None)

    def sync(self) -> None:
        if self.control is not None:
            self.control.setSelectedSegment_(self.speaker.mode_index())


class HoverSpeaker:
    def __init__(self) -> None:
        self.last_spoken: SpokenWord | None = None
        self.speech_process: subprocess.Popen | None = None
        self.speech_text: str | None = None
        self.selection_text: str | None = None
        self.selection_next_speak_at = 0.0
        self.last_selection_copy_at = 0.0
        self.accessibility_enabled = False
        self.warned_about_screenshot = False
        self.trigger_mode = TRIGGER_MODE if TRIGGER_MODE in TRIGGER_MODES else TRIGGER_BOTH
        self.app = None
        self.trigger_switch = None

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
        print("Tip: set HOVERSPEAK_VOICE=Daniel or HOVERSPEAK_RATE=170 before launching.")
        print(f"Trigger mode: {self.trigger_mode}. Highlight text to show the three-stage switch.")
        if SELECTION_ENABLED:
            print("Selection loop is on. Highlight text to repeat it; clear the highlight to return to hover mode.")
            if SELECTION_COPY_MODE == "auto":
                print("Selection copy fallback is auto. It is disabled while an editable field is focused.")
            elif SELECTION_COPY_MODE == "1":
                print("Selection copy fallback is on. It may briefly use Cmd+C when AXSelectedText is unavailable.")
            else:
                print("Selection copy fallback is off.")
        if not self.accessibility_enabled:
            print("Accessibility is not enabled, so HoverSpeak will use OCR-only mode.")
        if OCR_ENABLED:
            print("OCR fallback is on. If prompted, allow Screen Recording for this terminal app.")

        signal.signal(signal.SIGINT, self._stop)
        signal.signal(signal.SIGTERM, self._stop)

        while True:
            self._drain_app_events()
            if not self._handle_selected_text() and self.trigger_mode == TRIGGER_BOTH:
                self._speak_word_under_cursor()
            time.sleep(POLL_INTERVAL)

    def _setup_app(self) -> None:
        self.app = AppKit.NSApplication.sharedApplication()
        self.app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        self.trigger_switch = TriggerSwitchController.alloc().initWithSpeaker_(self)

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
        if self.trigger_switch is not None:
            self.trigger_switch.sync()
        print(f"Trigger mode changed: {mode}")

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
            word, description = self._accessibility_word_under_cursor(point)
        if not word and OCR_ENABLED:
            word = self._ocr_word_under_cursor(point)
            description = f"ocr:{int(point.x)}:{int(point.y)}"

        if not word or not self._is_speakable(word):
            return

        if self._should_skip(word, description):
            return

        self._speak(word)
        self.last_spoken = SpokenWord(word, description, time.monotonic())
        print(f"Spoke: {word}")

    def _handle_selected_text(self) -> bool:
        if not SELECTION_ENABLED or not self.accessibility_enabled:
            self._clear_selection_state()
            return False

        selected = self._selected_text_from_frontmost_app()
        if not selected:
            if self.selection_text is not None:
                self._clear_selection_state()
            if self.trigger_switch is not None:
                self.trigger_switch.hide()
            return False

        if self.trigger_switch is not None:
            self.trigger_switch.show_near_mouse()

        if selected != self.selection_text:
            self._stop_speech()
            self.selection_text = selected
            self.selection_next_speak_at = 0.0
            print(f"Selected: {selected[:80]}")

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
        self.selection_next_speak_at = 0.0

    def _selected_text_from_frontmost_app(self) -> str | None:
        app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return None

        app_element = AS.AXUIElementCreateApplication(app.processIdentifier())
        roots = [
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

        return None

    def _should_use_copy_fallback(self, app_element) -> bool:
        if SELECTION_COPY_MODE in ("0", "false", "off", "no"):
            return False
        if SELECTION_COPY_MODE in ("1", "true", "on", "yes"):
            return True
        return not self._focused_element_is_editable(app_element)

    def _focused_element_is_editable(self, app_element) -> bool:
        focused = self._attribute(app_element, "AXFocusedUIElement")
        if focused is None:
            return False

        role = self._string_attribute(focused, AS.kAXRoleAttribute) or ""
        subrole = self._string_attribute(focused, "AXSubrole") or ""
        if role in {"AXTextArea", "AXTextField", "AXComboBox", "AXSearchField"}:
            return True
        if subrole in {"AXTextAttachment", "AXSecureTextField"}:
            return True

        editable = self._attribute(focused, "AXEditable")
        if editable is not None:
            return bool(editable)

        writable = self._attribute_names(focused)
        return "AXSelectedTextRange" in writable and "AXValue" in writable

    def _selected_text_in_tree(self, root) -> str | None:
        queue = [(root, 0)]
        seen = set()

        while queue:
            element, depth = queue.pop(0)
            key = repr(element)
            if key in seen:
                continue
            seen.add(key)

            selected = self._clean_selected_text(self._string_attribute(element, AS.kAXSelectedTextAttribute))
            if selected:
                return selected

            if depth >= 8:
                continue

            for child_attr in CHILD_ATTRIBUTES:
                children = self._attribute(element, child_attr)
                if isinstance(children, (list, tuple)):
                    queue.extend((child, depth + 1) for child in children)

        return None

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
        source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
        command_down = Quartz.CGEventCreateKeyboardEvent(source, 55, True)
        c_down = Quartz.CGEventCreateKeyboardEvent(source, 8, True)
        c_up = Quartz.CGEventCreateKeyboardEvent(source, 8, False)
        command_up = Quartz.CGEventCreateKeyboardEvent(source, 55, False)

        Quartz.CGEventSetFlags(c_down, Quartz.kCGEventFlagMaskCommand)
        Quartz.CGEventSetFlags(c_up, Quartz.kCGEventFlagMaskCommand)

        for event in (command_down, c_down, c_up, command_up):
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

    def _accessibility_word_under_cursor(self, point) -> tuple[str | None, str]:
        system = AS.AXUIElementCreateSystemWide()
        result, element = AS.AXUIElementCopyElementAtPosition(system, point.x, point.y, None)
        if result != AS.kAXErrorSuccess or element is None:
            return None, "accessibility:none"

        for candidate in self._candidate_elements_at_point(point, element):
            word = self._word_at(point, candidate)
            if DEBUG:
                self._debug_element(candidate, point, word)
            if word:
                return word, self._describe(candidate)

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
        image = Quartz.CGWindowListCreateImage(
            crop,
            Quartz.kCGWindowListOptionOnScreenOnly,
            Quartz.kCGNullWindowID,
            Quartz.kCGWindowImageDefault,
        )
        if image is None and not self.warned_about_screenshot:
            print(
                "OCR could not capture the screen. Enable Screen Recording for this terminal app "
                "in System Settings > Privacy & Security > Screen & System Audio Recording."
            )
            self.warned_about_screenshot = True
        return image

    def _word_near_crop_center(self, lines: list[tuple[str, object]]) -> str | None:
        crop_origin = Quartz.CGPoint(0, 0)
        point = Quartz.CGPoint(OCR_WIDTH / 2, OCR_HEIGHT / 2)
        return self._word_near_point(lines, point, crop_origin)

    def _word_near_point(self, lines: list[tuple[str, object]], point, crop_origin) -> str | None:
        best_word = None
        best_score = float("inf")

        for text, box in lines:
            tokens = list(self._token_spans(text))
            if not tokens:
                continue

            line_rect = self._vision_box_to_screen_rect(box, crop_origin)
            for start, end, token in tokens:
                token_rect = self._estimate_token_rect(line_rect, text, start, end)
                score = self._distance_to_rect(token_rect, point)
                if self._rect_contains(token_rect, point):
                    score = 0
                if score < best_score:
                    best_score = score
                    best_word = token.strip()

        return best_word

    def _vision_box_to_screen_rect(self, box, crop_origin):
        x = crop_origin.x + box.origin.x * OCR_WIDTH
        y = crop_origin.y + (1 - box.origin.y - box.size.height) * OCR_HEIGHT
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

    def _distance_to_rect(self, rect, point) -> float:
        left = rect.origin.x
        right = rect.origin.x + rect.size.width
        top = rect.origin.y
        bottom = rect.origin.y + rect.size.height
        dx = max(left - point.x, 0, point.x - right)
        dy = max(top - point.y, 0, point.y - bottom)
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

        args = ["/usr/bin/say", "-r", RATE]
        if VOICE:
            args.extend(["-v", VOICE])
        args.append(word)
        self.speech_process = subprocess.Popen(args)
        self.speech_text = word

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
