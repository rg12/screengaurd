import Foundation
import SwiftUI

/// Owns the pipeline: microphone -> Deepgram -> transcript -> Claude -> suggested reply.
@MainActor
final class ConversationViewModel: ObservableObject {
    @Published var isListening = false
    @Published var audioSource: AudioSource = .microphone
    @Published var liveCaption = ""
    @Published var transcript: [String] = []
    @Published var replies: [String] = []
    @Published var status = "Choose an audio source, then start listening."
    @Published var errorMessage: String?

    private let capture = AudioCapture()
    private var deepgram: DeepgramClient?

    /// Trailing window of the conversation, same 8-message cap as the desktop build.
    private var history: [ClaudeClient.Message] = []
    private var replyTask: Task<Void, Never>?

    var hasDeepgramKey: Bool { KeychainStore.read(.deepgram) != nil }
    var hasAnthropicKey: Bool { KeychainStore.read(.anthropic) != nil }

    func toggleListening() {
        isListening ? stop() : start()
    }

    func start() {
        guard !isListening else { return }
        errorMessage = nil

        guard let deepgramKey = KeychainStore.read(.deepgram) else {
            errorMessage = "Add your Deepgram API key in Settings."
            return
        }

        Task {
            guard await AudioCapture.requestPermission() else {
                errorMessage = AudioCapture.CaptureError.permissionDenied.localizedDescription
                return
            }
            beginStreaming(deepgramKey: deepgramKey)
        }
    }

    private func beginStreaming(deepgramKey: String) {
        let client = DeepgramClient(apiKey: deepgramKey)
        deepgram = client

        client.onOpen = { [weak self] in
            Task { @MainActor in self?.status = "Listening…" }
        }
        client.onTranscript = { [weak self] text, isFinal in
            Task { @MainActor in self?.handleTranscript(text, isFinal: isFinal) }
        }
        client.onError = { [weak self] error in
            Task { @MainActor in self?.fail(error.localizedDescription) }
        }

        capture.onPCM = { [weak client] pcm in client?.send(pcm) }

        do {
            try client.connect()
            try capture.start(source: audioSource)
            isListening = true
            liveCaption = ""
            status = "Connecting to Deepgram…"
        } catch {
            fail(error.localizedDescription)
        }
    }

    func stop() {
        capture.stop()
        capture.onPCM = nil
        deepgram?.finish()
        deepgram = nil
        isListening = false
        liveCaption = ""
        status = "Stopped."
    }

    private func fail(_ message: String) {
        errorMessage = message
        if isListening { stop() }
        status = "Stopped after an error."
    }

    private func handleTranscript(_ text: String, isFinal: Bool) {
        guard isFinal else {
            liveCaption = text
            return
        }

        liveCaption = ""
        transcript.append(text)
        requestReply(for: text)
    }

    private func requestReply(for utterance: String) {
        guard let apiKey = KeychainStore.read(.anthropic) else {
            status = "Add your Anthropic API key in Settings to get replies."
            return
        }

        // One reply at a time — a new utterance supersedes an in-flight suggestion.
        replyTask?.cancel()
        replyTask = Task { [weak self] in
            guard let self else { return }

            let messages = self.history + [
                .init(role: "user", content: "The other person just said:\n\(utterance)")
            ]

            self.status = "Generating a reply…"
            self.replies.append("")

            do {
                let reply = try await ClaudeClient(apiKey: apiKey).streamReply(messages: messages) { delta in
                    Task { @MainActor in
                        guard !self.replies.isEmpty else { return }
                        self.replies[self.replies.count - 1] += delta
                    }
                }
                guard !Task.isCancelled else { return }

                self.history.append(.init(role: "user", content: utterance))
                self.history.append(.init(role: "assistant", content: reply))
                self.history = Array(self.history.suffix(8))
                self.status = "Ready for the next sentence."
            } catch is CancellationError {
                return
            } catch {
                if !self.replies.isEmpty, self.replies[self.replies.count - 1].isEmpty {
                    self.replies.removeLast()
                }
                self.errorMessage = error.localizedDescription
                self.status = "Reply failed."
            }
        }
    }

    func clear() {
        transcript.removeAll()
        replies.removeAll()
        history.removeAll()
        liveCaption = ""
    }
}
