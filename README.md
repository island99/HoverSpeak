# HoverSpeak

HoverSpeak is a tiny macOS prototype that speaks a word when your cursor hovers over selectable text.

## Run the Python version

This is the recommended route in the current workspace because the installed macOS Command Line Tools have a Swift compiler/SDK mismatch.

```bash
cd HoverSpeak
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python hoverspeak.py
```

On first launch, macOS should ask for Accessibility permission. If it does not, open:

`System Settings > Privacy & Security > Accessibility`

Then enable the terminal app you launched HoverSpeak from, such as Terminal, iTerm, or Codex.

## Run the Swift version

```bash
cd HoverSpeak
swift run
```

If `swift run` fails with a Swift compiler/SDK mismatch, install or select a matching Xcode/Command Line Tools version, or use the Python version above.

## Options

```bash
HOVERSPEAK_RATE=170 swift run
HOVERSPEAK_VOICE=Daniel swift run
HOVERSPEAK_VOICE=Mei-Jia HOVERSPEAK_RATE=180 swift run
```

For the Python version, use the same environment variables:

```bash
HOVERSPEAK_RATE=170 ./run.sh
HOVERSPEAK_VOICE=Daniel ./run.sh
HOVERSPEAK_OCR=0 ./run.sh
HOVERSPEAK_OCR_LANGUAGES=zh-Hans,en-US ./run.sh
HOVERSPEAK_SELECTION=0 ./run.sh
HOVERSPEAK_SELECTION_PAUSE=1.2 ./run.sh
HOVERSPEAK_SELECTION_COPY=auto ./run.sh
HOVERSPEAK_SELECTION_COPY=0 ./run.sh
HOVERSPEAK_SELECTION_COPY=1 ./run.sh
HOVERSPEAK_CJK_CHARS=4 ./run.sh
HOVERSPEAK_OCR_WIDTH=300 HOVERSPEAK_OCR_HEIGHT=80 ./run.sh
```

OCR fallback is enabled by default. It helps with software that renders text visually but does not expose word positions through Accessibility. macOS may also require Screen Recording permission for your terminal app:

`System Settings > Privacy & Security > Screen & System Audio Recording`

Selection loop is enabled by default. Highlight text to repeatedly speak the selected text; clear the highlight to return to hover mode. The `Cmd+C` fallback defaults to `auto`: it helps with apps that do not expose selected text through Accessibility, but is disabled while an editable text field is focused.

List available voices:

```bash
say -v '?'
```

## Current limitations

- Works best in native macOS text controls and apps that expose text through Accessibility.
- OCR fallback can read many custom-rendered apps, PDFs, and images, but it is less precise than Accessibility.
- Screen Recording permission may be needed for OCR.
