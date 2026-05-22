# HoverSpeak

HoverSpeak 是一个 macOS 桌面辅助小工具：当你把鼠标停在文字附近，或刷住/高亮一段文字时，它会用系统语音把对应内容读出来。

它适合用来做英语单词发音、快速听读句子、阅读软件里的文本内容。发音使用 macOS 本地的 `say` 命令，不需要联网。

English README: [README.md](README.md)

## 功能

- 鼠标附近发音：鼠标停在文字附近一小段时间后，读出圈内识别到的文字。
- 高亮文本循环发音：刷住/高亮一段文字后，循环朗读这段文本。
- 三档触发开关：高亮文字后，鼠标旁边会出现一个迷你开关。
- 圈内识别提示：开启鼠标附近发音时，鼠标旁边会显示一个半透明圆形区域。
- 停留反馈：识别区域默认是淡青蓝色，停留到可发音状态后变成淡薄荷绿。
- OCR 兜底：对不暴露标准文本接口的软件，也会尝试用屏幕 OCR 识别。
- 中英文语音：会根据文本自动选择中文或英文语音，并给中文使用更慢一点的语速。

## 推荐运行方式

当前推荐使用 Python 版本。Swift 版本还保留在仓库里，但在部分 macOS Command Line Tools 环境下可能遇到 Swift 编译器和 SDK 不匹配的问题。

```bash
cd HoverSpeak
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python hoverspeak.py
```

也可以直接使用仓库里的启动脚本：

```bash
./run.sh
```

## macOS 权限

第一次启动时，macOS 通常会提示开启辅助功能权限。如果没有弹窗，可以手动打开：

```text
系统设置 > 隐私与安全性 > 辅助功能
```

然后允许你用来启动 HoverSpeak 的 App，例如 Terminal、iTerm 或 Codex。

如果要使用 OCR 识别屏幕中的文字，还需要屏幕录制权限：

```text
系统设置 > 隐私与安全性 > 屏幕与系统音频录制
```

同样允许启动 HoverSpeak 的 App。

## 三档开关

当你高亮一段文本后，HoverSpeak 会在鼠标旁边显示一个小开关：

- 静音图标：关闭发声，任何时候都不读。
- 文本图标：只读高亮/刷住的文本。
- 喇叭图标：读高亮文本，也读鼠标附近的文本。

开关只会在新的高亮文本出现时显示一次，不会跟着鼠标移动。点击开关后会立即切换模式并消失；如果不点击，默认 2.5 秒后自动消失。点击开关以外的区域也会让它消失，直到下一次高亮文本再出现。

## 鼠标附近发音

当开关处于“高亮文本 + 鼠标附近文本”模式，并且当前没有高亮文本时，鼠标旁边会出现一个半透明圆形区域。

HoverSpeak 会尽量只读圆形区域内的文字：

- 中文会按字符位置裁剪，只保留圈内文字。
- 英文会尽量按完整单词处理，圈边切到的残缺单词会被丢掉。
- OCR 会优先匹配鼠标所在的当前文本行，减少读到上一行或下一行的情况。
- 鼠标需要停留超过设定时间后才发音，避免移动过程中误读。

如果你感觉识别位置整体偏上或偏下，可以用 `HOVERSPEAK_OCR_Y_OFFSET` 做轻微校准。

## 常用配置

这些配置都可以放在 `./run.sh` 前面临时使用：

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
HOVERSPEAK_SELECTION_COPY=auto ./run.sh
HOVERSPEAK_SELECTION_COPY=0 ./run.sh
HOVERSPEAK_SELECTION_COPY=1 ./run.sh
```

### 配置说明

| 变量 | 作用 |
| --- | --- |
| `HOVERSPEAK_TRIGGER_MODE` | 默认触发模式：`off`、`selection`、`both` |
| `HOVERSPEAK_CJK_RATE` | 中文语速 |
| `HOVERSPEAK_LATIN_RATE` | 英文语速 |
| `HOVERSPEAK_VOICE` | 强制指定 macOS 语音 |
| `HOVERSPEAK_AUTO_VOICE` | 是否根据文本自动选择中英文语音 |
| `HOVERSPEAK_CURSOR_REGION` | 鼠标附近识别圆圈的直径 |
| `HOVERSPEAK_HOVER_DWELL` | 鼠标停留多久后才读附近文字 |
| `HOVERSPEAK_SWITCH_AUTO_HIDE` | 高亮后开关多久自动消失，设为 `0` 可关闭自动消失 |
| `HOVERSPEAK_OCR` | 是否启用 OCR |
| `HOVERSPEAK_OCR_Y_OFFSET` | OCR 结果的上下校准 |
| `HOVERSPEAK_SELECTION_PAUSE` | 高亮文本循环朗读间隔 |
| `HOVERSPEAK_SELECTION_COPY` | 是否用复制作为选中文本兜底：`auto`、`0`、`1` |

查看系统可用语音：

```bash
say -v '?'
```

## Swift 版本

如果你的 Xcode 或 Command Line Tools 环境匹配，也可以尝试：

```bash
cd HoverSpeak
swift run
```

如果遇到 Swift 编译器/SDK 不匹配，请优先使用 Python 版本。

## 当前限制

- 在原生 macOS 文本控件里效果最好。
- 对自绘界面、PDF、图片或复杂 App，OCR 可以兜底，但精度会受字体、缩放、背景和行距影响。
- OCR 需要屏幕录制权限。
- 鼠标附近识别不是系统级“真实文本选择”，而是 Accessibility 和 OCR 的组合，因此极端场景下仍可能需要调参。
