import AppKit
import ApplicationServices
import Foundation

private let pollInterval: TimeInterval = 0.28
private let repeatCooldown: TimeInterval = 1.4
private let defaultVoice = ProcessInfo.processInfo.environment["HOVERSPEAK_VOICE"]
private let defaultRate = ProcessInfo.processInfo.environment["HOVERSPEAK_RATE"] ?? "185"

private struct SpokenWord {
    let text: String
    let elementDescription: String
    let spokenAt: Date
}

private final class HoverSpeaker {
    private var lastSpoken: SpokenWord?
    private var speechProcess: Process?

    func start() {
        guard requestAccessibilityAccess() else {
            print("HoverSpeak needs Accessibility permission. Enable it in System Settings > Privacy & Security > Accessibility, then run again.")
            exit(1)
        }

        print("HoverSpeak is running. Move the cursor over selectable text. Press Ctrl+C to stop.")
        print("Tip: set HOVERSPEAK_VOICE=Daniel or HOVERSPEAK_RATE=170 before launching to tune speech.")

        Timer.scheduledTimer(withTimeInterval: pollInterval, repeats: true) { [weak self] _ in
            self?.speakWordUnderCursor()
        }

        RunLoop.main.run()
    }

    private func requestAccessibilityAccess() -> Bool {
        let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
        return AXIsProcessTrustedWithOptions(options)
    }

    private func speakWordUnderCursor() {
        let mouse = NSEvent.mouseLocation
        let point = CGPoint(x: mouse.x, y: NSScreen.screensFrameHeight - mouse.y)
        let systemWide = AXUIElementCreateSystemWide()

        var rawElement: AXUIElement?
        let result = AXUIElementCopyElementAtPosition(systemWide, Float(point.x), Float(point.y), &rawElement)
        guard result == .success, let element = rawElement else {
            return
        }

        guard let word = word(at: point, in: element), isSpeakable(word) else {
            return
        }

        let elementDescription = describe(element)
        if shouldSkip(word, elementDescription: elementDescription) {
            return
        }

        speak(word)
        lastSpoken = SpokenWord(text: word, elementDescription: elementDescription, spokenAt: Date())
        print("Spoke: \(word)")
    }

    private func word(at point: CGPoint, in element: AXUIElement) -> String? {
        if let range = rangeForPosition(point, in: element),
           let fullText = stringAttribute(kAXValueAttribute, from: element),
           let word = wordAround(location: range.location, in: fullText) {
            return word
        }

        if let selectedText = stringAttribute(kAXSelectedTextAttribute, from: element),
           isSpeakable(selectedText) {
            return selectedText
        }

        if let title = stringAttribute(kAXTitleAttribute, from: element),
           isSpeakable(title), title.count <= 40 {
            return title
        }

        return nil
    }

    private func rangeForPosition(_ point: CGPoint, in element: AXUIElement) -> CFRange? {
        var mutablePoint = point
        guard let pointValue = AXValueCreate(.cgPoint, &mutablePoint) else {
            return nil
        }

        var rawRange: CFTypeRef?
        let result = AXUIElementCopyParameterizedAttributeValue(
            element,
            "AXRangeForPosition" as CFString,
            pointValue,
            &rawRange
        )

        guard result == .success, let rangeValue = rawRange, CFGetTypeID(rangeValue) == AXValueGetTypeID() else {
            return nil
        }

        var range = CFRange()
        let ok = AXValueGetValue(rangeValue as! AXValue, .cfRange, &range)
        return ok ? range : nil
    }

    private func stringAttribute(_ attribute: CFString, from element: AXUIElement) -> String? {
        var rawValue: CFTypeRef?
        let result = AXUIElementCopyAttributeValue(element, attribute, &rawValue)
        guard result == .success, let value = rawValue as? String else {
            return nil
        }
        return value
    }

    private func wordAround(location: CFIndex, in text: String) -> String? {
        guard !text.isEmpty else {
            return nil
        }

        let clampedOffset = max(0, min(location, text.count - 1))
        guard let center = text.index(text.startIndex, offsetBy: clampedOffset, limitedBy: text.endIndex) else {
            return nil
        }

        var start = center
        while start > text.startIndex {
            let previous = text.index(before: start)
            if !isWordCharacter(text[previous]) {
                break
            }
            start = previous
        }

        var end = center
        while end < text.endIndex, isWordCharacter(text[end]) {
            end = text.index(after: end)
        }

        let word = String(text[start..<end]).trimmingCharacters(in: .whitespacesAndNewlines)
        return word.isEmpty ? nil : word
    }

    private func isWordCharacter(_ character: Character) -> Bool {
        character.unicodeScalars.allSatisfy { scalar in
            CharacterSet.letters.contains(scalar)
                || CharacterSet.decimalDigits.contains(scalar)
                || scalar == "'"
                || scalar == "-"
        }
    }

    private func isSpeakable(_ text: String) -> Bool {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard (2...40).contains(trimmed.count) else {
            return false
        }
        return trimmed.rangeOfCharacter(from: .letters) != nil
    }

    private func shouldSkip(_ word: String, elementDescription: String) -> Bool {
        guard let lastSpoken else {
            return false
        }

        let elapsed = Date().timeIntervalSince(lastSpoken.spokenAt)
        return lastSpoken.text.caseInsensitiveCompare(word) == .orderedSame
            && lastSpoken.elementDescription == elementDescription
            && elapsed < repeatCooldown
    }

    private func speak(_ word: String) {
        speechProcess?.terminate()

        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/say")

        var arguments = ["-r", defaultRate]
        if let defaultVoice, !defaultVoice.isEmpty {
            arguments += ["-v", defaultVoice]
        }
        arguments.append(word)
        process.arguments = arguments

        do {
            try process.run()
            speechProcess = process
        } catch {
            print("Failed to speak \(word): \(error.localizedDescription)")
        }
    }

    private func describe(_ element: AXUIElement) -> String {
        let role = stringAttribute(kAXRoleAttribute, from: element) ?? "unknown"
        let title = stringAttribute(kAXTitleAttribute, from: element) ?? ""
        return "\(role):\(title)"
    }
}

private extension Array where Element == NSScreen {
    var screensFrameHeight: CGFloat {
        map(\.frame).reduce(CGFloat(0)) { max($0, $1.maxY) }
    }
}

HoverSpeaker().start()
