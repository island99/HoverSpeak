# HoverSpeak

HoverSpeak is a small macOS desktop helper that speaks text when you hover near it or highlight/select it.

It is useful for quick word pronunciation, listening to short sentences, and reading text inside apps. Speech is handled locally through macOS `say`; no network connection is required.

中文说明：[README.zh-CN.md](README.zh-CN.md)

## Features

- Nearby text speaking: hover near text for a short moment and HoverSpeak reads the text inside the cursor region.
- Highlighted text loop: highlight/select text and HoverSpeak repeatedly reads that selection.
- Three-stage trigger switch: a compact switch appears near the cursor after text is highlighted.
- Cursor region indicator: when nearby text speaking is enabled, a translucent circular region appears near the cursor.
- Dwell feedback: the region starts as soft cyan-blue and turns soft mint-green once the current text is ready to speak.
- OCR fallback: apps that do not expose standard text through Accessibility can still be read through screen OCR.
- Chinese and English voices: HoverSpeak can pick a local Chinese or English voice based on the text, with a slower default rate for Chinese snippets.

## Recommended Setup

The Python version is currently recommended. The Swift version is still included, but some macOS Command Line Tools installations may hit a Swift compiler/SDK mismatch.

```bash
cd HoverSpeak
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python hoverspeak.py
```

You can also use the included launcher:

```bash
./run.sh
```

## macOS Permissions

On first launch, macOS should ask for Accessibility permission. If it does not, open:

```text
System Settings > Privacy & Security > Accessibility
```

Enable the app you used to launch HoverSpeak, such as Terminal, iTerm, or Codex.

OCR also requires Screen Recording permission:

```text
System Settings > Privacy & Security > Screen & System Audio Recording
```

Enable the same launcher app there.

## Three-Stage Switch

When text is highlighted, HoverSpeak shows a small switch beside the cursor:

- Muted speaker icon: never speak.
- Text icon: speak only highlighted/selected text.
- Speaker icon: speak highlighted text and nearby text when nothing is highlighted.

The switch is placed once for each new highlight and does not follow the cursor. Clicking a switch option changes the mode and hides the switch immediately. If it is not clicked, it hides automatically after 2.5 seconds by default. Clicking outside the switch also hides it until the next highlighted text selection.

## Nearby Text Speaking

When the switch is in "highlight + nearby text" mode and no text is highlighted, HoverSpeak shows a translucent circular region near the cursor.

HoverSpeak tries to speak only the text inside that circle:

- Chinese text is clipped by character position.
- English text is handled as whole words when possible; clipped edge fragments are dropped.
- OCR prefers the cursor's current text row, reducing accidental reads from the row above or below.
- The cursor must dwell on the same candidate text before speech starts, which reduces misreads while moving.

If the OCR result feels consistently shifted up or down on your display, tune it with `HOVERSPEAK_OCR_Y_OFFSET`.

## Common Options

Prefix `./run.sh` with any of these environment variables:

```bash
HOVERSPEAK_TRIGGER_MODE=off ./run.sh
HOVERSPEAK_TRIGGER_MODE=selection ./run.sh
HOVERSPEAK_TRIGGER_MODE=both ./run.sh

HOVERSPEAK_CJK_RATE=150 ./run.sh
HOVERSPEAK_LATIN_RATE=170 ./run.sh
HOVERSPEAK_VOICE=Daniel ./run.sh
HOVERSPEAK_AUTO_VOICE=0 ./run.sh

HOVERSPEAK_CURSOR_REGION=72 ./run.sh
HOVERSPEAK_HOVER_DWELL=0.8 ./run.sh
HOVERSPEAK_SWITCH_AUTO_HIDE=2.5 ./run.sh

HOVERSPEAK_OCR=0 ./run.sh
HOVERSPEAK_OCR_LANGUAGES=zh-Hans,en-US ./run.sh
HOVERSPEAK_OCR_WIDTH=300 HOVERSPEAK_OCR_HEIGHT=80 ./run.sh
HOVERSPEAK_OCR_LINE_MAX_CHARS=18 ./run.sh
HOVERSPEAK_OCR_Y_OFFSET=4 ./run.sh

HOVERSPEAK_SELECTION=0 ./run.sh
HOVERSPEAK_SELECTION_PAUSE=1.2 ./run.sh
HOVERSPEAK_SELECTION_COPY=off ./run.sh
HOVERSPEAK_UNSAFE_KEYBOARD_COPY=1 HOVERSPEAK_SELECTION_COPY=1 ./run.sh
```

### Option Reference

| Variable | Purpose |
| --- | --- |
| `HOVERSPEAK_TRIGGER_MODE` | Initial trigger mode: `off`, `selection`, or `both` |
| `HOVERSPEAK_CJK_RATE` | Chinese speech rate |
| `HOVERSPEAK_LATIN_RATE` | English/Latin speech rate |
| `HOVERSPEAK_VOICE` | Force a specific macOS voice |
| `HOVERSPEAK_AUTO_VOICE` | Automatically choose Chinese or English voice based on text |
| `HOVERSPEAK_CURSOR_REGION` | Diameter of the nearby-text cursor circle |
| `HOVERSPEAK_HOVER_DWELL` | How long the cursor must stay before nearby text is spoken |
| `HOVERSPEAK_SWITCH_AUTO_HIDE` | Seconds before the highlight switch hides automatically; set `0` to disable |
| `HOVERSPEAK_OCR` | Enable or disable OCR |
| `HOVERSPEAK_OCR_Y_OFFSET` | Vertical calibration for OCR results |
| `HOVERSPEAK_SELECTION_PAUSE` | Repeat interval for highlighted text |
| `HOVERSPEAK_SELECTION_COPY` | Clipboard fallback mode. Defaults to `menu`; uses the app's Edit > Copy menu item via Accessibility, which is safe and never sends keyboard events. Set to `off` to disable entirely |
| `HOVERSPEAK_UNSAFE_KEYBOARD_COPY` | Set to `1` only if you accept HoverSpeak sending `Cmd+C` as a fallback |

By default, HoverSpeak never sends keyboard shortcuts. This avoids stray `c` input while typing in browsers, input methods, or editable fields.

List available system voices:

```bash
say -v '?'
```

## Swift Version

If your Xcode or Command Line Tools environment is compatible, you can try:

```bash
cd HoverSpeak
swift run
```

If you hit a Swift compiler/SDK mismatch, use the Python version instead.

## Current Limitations

- Works best with native macOS text controls.
- OCR can help with custom-rendered apps, PDFs, images, and complex interfaces, but precision depends on fonts, scale, backgrounds, and line spacing.
- OCR requires Screen Recording permission.
- Nearby text detection is a combination of Accessibility and OCR, not a system-level text selection, so some edge cases may still need tuning.
