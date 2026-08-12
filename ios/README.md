# iOS Conversation Assistant

SwiftUI port of the speech-to-text + suggested-reply pipeline from `tray_app.py`:
microphone → Deepgram (`nova-3`, streaming) → transcript → Claude (`claude-sonnet-5`,
streaming) → suggested reply.

## What does and does not carry over from the Windows build

| Feature | Windows | iOS |
| --- | --- | --- |
| Deepgram live transcription | ✅ | ✅ |
| Claude streaming replies | ✅ | ✅ |
| Microphone capture | ✅ | ✅ |
| **Far-end call audio (WASAPI loopback)** | ✅ | ❌ **not possible** |
| Floating always-on-top overlay | ✅ | ❌ not possible |
| Hidden from screen capture | ✅ | ❌ no iOS equivalent |

### About call audio

iOS exposes no API that gives an app the far end of a phone or VoIP call. This is a
sandbox restriction, not a gap in this code — there is nothing to implement against.
`AudioSource.speakerphone` is the practical workaround: put the call on speaker and
the microphone picks up both voices acoustically.

That mode deliberately uses `AVAudioSession.Mode.default` rather than `.voiceChat`,
because `.voiceChat` enables voice processing whose echo canceller strips speaker
output out of the mic signal — removing exactly the far-end audio you want. It also
sets `.mixWithOthers` so it can coexist with the app running the call.

Note that iOS still gives a live call priority over the audio session, so capture can
be interrupted or denied while another app holds the mic for a call. Test this on a
real device — the Simulator does not reproduce audio-session contention.

## Building

These are source files only; there is no `.xcodeproj` checked in. To build:

1. On a Mac, open Xcode → **File → New → Project → iOS → App**.
   - Interface: **SwiftUI**, Language: **Swift**
   - Product name: `ConversationAssistant`
2. Delete the generated `ContentView.swift` and `…App.swift`.
3. Drag everything in `Sources/` into the project ("Copy items if needed" checked).
4. In the target's **Info** tab, add:
   - `NSMicrophoneUsageDescription` — e.g. *"Transcribes conversation audio to generate
     suggested replies."* The app crashes on launch without this.
5. To keep transcribing while backgrounded, enable **Signing & Capabilities →
   Background Modes → Audio, AirPlay, and Picture in Picture**.
6. Build to a real device (microphone capture is unreliable in the Simulator).

Minimum deployment target: **iOS 16** (`NavigationStack`, `URLSession.bytes`).

## API keys

Enter both keys via the key icon in the top-right. They are stored in the iOS keychain
(`kSecClassGenericPassword`), mirroring how the desktop build uses `keyring`.

**If you ever distribute this**, do not ship the Anthropic key inside the app — keys in
a mobile binary can be extracted. Put a small server in front and have the app call
that instead.

## Layout

| File | Role |
| --- | --- |
| `ConversationAssistantApp.swift` | App entry point |
| `ContentView.swift` | Main UI + API-key settings sheet |
| `ConversationViewModel.swift` | Pipeline orchestration, conversation history |
| `AudioCapture.swift` | `AVAudioEngine` capture, resampling to 16 kHz mono PCM |
| `DeepgramClient.swift` | Streaming websocket transcription |
| `ClaudeClient.swift` | Streaming reply generation |
| `KeychainStore.swift` | API key storage |

## Legal

Recording a conversation requires consent from everyone involved in many
jurisdictions, and both app stores require you to disclose recording to the user.
