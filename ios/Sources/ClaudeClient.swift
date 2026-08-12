import Foundation

/// Streams a suggested reply from Claude. Mirrors the desktop build's streaming
/// path so words show up as they are generated rather than after the whole
/// response lands — that is most of the perceived latency win.
struct ClaudeClient {
    struct Message {
        let role: String
        let content: String

        var payload: [String: String] { ["role": role, "content": content] }
    }

    enum ClaudeError: LocalizedError {
        case http(status: Int, body: String)
        case emptyResponse

        var errorDescription: String? {
            switch self {
            case .http(401, _):
                return "Anthropic rejected the API key."
            case .http(429, _):
                return "Rate limited by Anthropic — wait a moment and try again."
            case .http(let status, let body):
                return "Anthropic error \(status): \(body.prefix(200))"
            case .emptyResponse:
                return "Claude returned an empty response."
            }
        }
    }

    static let systemPrompt = """
        You are a real-time conversation assistant. Preserve the intended meaning, silently \
        translate non-English input into English, and propose a polite, useful reply. \
        Never claim the user said something that is not in the transcript. Reply in clear, \
        natural English with only one or two concise sentences and no preamble.
        """

    private static let endpoint = URL(string: "https://api.anthropic.com/v1/messages")!
    private static let model = "claude-sonnet-5"
    private static let maxTokens = 160

    let apiKey: String

    /// Streams the reply, invoking `onDelta` per token chunk, and returns the full text.
    func streamReply(
        messages: [Message],
        onDelta: @escaping (String) -> Void
    ) async throws -> String {
        var request = URLRequest(url: Self.endpoint)
        request.httpMethod = "POST"
        request.setValue(apiKey, forHTTPHeaderField: "x-api-key")
        request.setValue("2023-06-01", forHTTPHeaderField: "anthropic-version")
        request.setValue("application/json", forHTTPHeaderField: "content-type")
        request.httpBody = try JSONSerialization.data(withJSONObject: [
            "model": Self.model,
            "max_tokens": Self.maxTokens,
            "stream": true,
            "system": Self.systemPrompt,
            "messages": messages.map(\.payload),
        ])

        let (bytes, response) = try await URLSession.shared.bytes(for: request)

        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            var body = ""
            for try await line in bytes.lines { body += line }
            throw ClaudeError.http(status: http.statusCode, body: body)
        }

        var full = ""
        for try await line in bytes.lines {
            guard line.hasPrefix("data:") else { continue }
            let payload = line.dropFirst("data:".count).trimmingCharacters(in: .whitespaces)
            guard payload != "[DONE]",
                  let data = payload.data(using: .utf8),
                  let event = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
            else { continue }

            if event["type"] as? String == "content_block_delta",
               let delta = event["delta"] as? [String: Any],
               let text = delta["text"] as? String {
                full += text
                onDelta(text)
            }
        }

        let reply = full.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !reply.isEmpty else { throw ClaudeError.emptyResponse }
        return reply
    }
}
