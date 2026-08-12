import Foundation

/// Live transcription over Deepgram's streaming websocket — the same `nova-3`
/// multilingual setup the Windows build uses, so transcripts match across both.
final class DeepgramClient: NSObject {
    enum DeepgramError: LocalizedError {
        case badURL
        case socketClosed(code: URLSessionWebSocketTask.CloseCode)

        var errorDescription: String? {
            switch self {
            case .badURL:
                return "Could not build the Deepgram stream URL."
            case .socketClosed(let code) where code == .policyViolation:
                return "Deepgram rejected the connection — check that the API key is valid."
            case .socketClosed(let code):
                return "Deepgram connection closed (code \(code.rawValue))."
            }
        }
    }

    private let apiKey: String
    private var task: URLSessionWebSocketTask?
    private lazy var session = URLSession(configuration: .default, delegate: self, delegateQueue: nil)

    /// `(transcript, isFinal)` — interim results stream in as a live caption,
    /// finals are what get sent on to Claude.
    var onTranscript: ((String, Bool) -> Void)?
    var onOpen: (() -> Void)?
    var onError: ((Error) -> Void)?

    init(apiKey: String) {
        self.apiKey = apiKey
        super.init()
    }

    func connect(sampleRate: Int = Int(AudioCapture.targetSampleRate), channels: Int = 1) throws {
        var components = URLComponents(string: "wss://api.deepgram.com/v1/listen")
        components?.queryItems = [
            URLQueryItem(name: "model", value: "nova-3"),
            URLQueryItem(name: "language", value: "multi"),
            URLQueryItem(name: "encoding", value: "linear16"),
            URLQueryItem(name: "sample_rate", value: String(sampleRate)),
            URLQueryItem(name: "channels", value: String(channels)),
            URLQueryItem(name: "interim_results", value: "true"),
            URLQueryItem(name: "punctuate", value: "true"),
            URLQueryItem(name: "endpointing", value: "300"),
        ]
        guard let url = components?.url else { throw DeepgramError.badURL }

        var request = URLRequest(url: url)
        request.setValue("Token \(apiKey)", forHTTPHeaderField: "Authorization")

        let task = session.webSocketTask(with: request)
        self.task = task
        task.resume()
        receive()
    }

    func send(_ pcm: Data) {
        task?.send(.data(pcm)) { [weak self] error in
            if let error { self?.onError?(error) }
        }
    }

    func finish() {
        // CloseStream asks Deepgram to flush any buffered audio before hanging up.
        task?.send(.string(#"{"type":"CloseStream"}"#)) { _ in }
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
    }

    private func receive() {
        task?.receive { [weak self] result in
            guard let self else { return }
            switch result {
            case .failure(let error):
                self.onError?(error)
            case .success(let message):
                switch message {
                case .string(let text):
                    self.handle(text)
                case .data(let data):
                    if let text = String(data: data, encoding: .utf8) { self.handle(text) }
                @unknown default:
                    break
                }
                self.receive()
            }
        }
    }

    private func handle(_ text: String) {
        guard
            let data = text.data(using: .utf8),
            let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let channel = root["channel"] as? [String: Any],
            let alternatives = channel["alternatives"] as? [[String: Any]],
            let transcript = alternatives.first?["transcript"] as? String,
            !transcript.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        else { return }

        onTranscript?(transcript, root["is_final"] as? Bool ?? false)
    }
}

extension DeepgramClient: URLSessionWebSocketDelegate {
    func urlSession(
        _ session: URLSession,
        webSocketTask: URLSessionWebSocketTask,
        didOpenWithProtocol protocol: String?
    ) {
        onOpen?()
    }

    func urlSession(
        _ session: URLSession,
        webSocketTask: URLSessionWebSocketTask,
        didCloseWith closeCode: URLSessionWebSocketTask.CloseCode,
        reason: Data?
    ) {
        guard closeCode != .normalClosure, closeCode != .goingAway else { return }
        onError?(DeepgramError.socketClosed(code: closeCode))
    }
}
