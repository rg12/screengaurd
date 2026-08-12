import AVFoundation

/// Where the audio we transcribe comes from.
///
/// iOS has no equivalent of the Windows build's WASAPI loopback: there is no API
/// that hands an app the far end of a phone or VoIP call. `.speakerphone` is the
/// closest thing that actually works — put the call on speaker and the mic picks
/// up both voices acoustically.
enum AudioSource: String, CaseIterable, Identifiable {
    case microphone = "Microphone"
    case speakerphone = "Speakerphone (both sides)"

    var id: String { rawValue }

    var explanation: String {
        switch self {
        case .microphone:
            return "Captures your device microphone — good for in-person conversations."
        case .speakerphone:
            return "Put the call on speaker. The mic then hears both sides. iOS cannot tap call audio directly."
        }
    }
}

/// Captures microphone audio and emits 16 kHz mono linear16 PCM, which is the
/// format the Deepgram socket is opened with.
final class AudioCapture {
    enum CaptureError: LocalizedError {
        case unsupportedFormat
        case converterUnavailable
        case permissionDenied

        var errorDescription: String? {
            switch self {
            case .unsupportedFormat: return "Could not build a 16 kHz mono audio format."
            case .converterUnavailable: return "Could not create an audio converter for the input device."
            case .permissionDenied: return "Microphone access was denied. Enable it in Settings."
            }
        }
    }

    static let targetSampleRate: Double = 16_000
    static let channelCount: AVAudioChannelCount = 1

    private let engine = AVAudioEngine()
    private var converter: AVAudioConverter?
    private var isRunning = false

    /// Called on the audio thread with each converted PCM chunk.
    var onPCM: ((Data) -> Void)?

    static func requestPermission() async -> Bool {
        await withCheckedContinuation { continuation in
            if #available(iOS 17.0, *) {
                AVAudioApplication.requestRecordPermission { continuation.resume(returning: $0) }
            } else {
                AVAudioSession.sharedInstance().requestRecordPermission { continuation.resume(returning: $0) }
            }
        }
    }

    func start(source: AudioSource) throws {
        guard !isRunning else { return }

        try configureSession(for: source)

        let input = engine.inputNode
        let inputFormat = input.outputFormat(forBus: 0)
        guard let outputFormat = AVAudioFormat(
            commonFormat: .pcmFormatInt16,
            sampleRate: Self.targetSampleRate,
            channels: Self.channelCount,
            interleaved: true
        ) else { throw CaptureError.unsupportedFormat }

        guard let converter = AVAudioConverter(from: inputFormat, to: outputFormat) else {
            throw CaptureError.converterUnavailable
        }
        self.converter = converter

        input.installTap(onBus: 0, bufferSize: 4096, format: inputFormat) { [weak self] buffer, _ in
            self?.emit(buffer, as: outputFormat)
        }

        engine.prepare()
        try engine.start()
        isRunning = true
    }

    func stop() {
        guard isRunning else { return }
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        converter = nil
        isRunning = false
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }

    private func configureSession(for source: AudioSource) throws {
        let session = AVAudioSession.sharedInstance()

        switch source {
        case .microphone:
            try session.setCategory(.record, mode: .measurement, options: [])
        case .speakerphone:
            // Deliberately NOT `.voiceChat`: that turns on voice processing, whose
            // echo canceller removes speaker output from the mic signal — which is
            // exactly the far-end voice we are trying to transcribe here.
            // `.mixWithOthers` lets us coexist with the app running the call.
            try session.setCategory(
                .playAndRecord,
                mode: .default,
                options: [.defaultToSpeaker, .allowBluetoothA2DP, .mixWithOthers]
            )
        }

        try session.setPreferredSampleRate(Self.targetSampleRate)
        try session.setActive(true)
    }

    /// Resamples one tap buffer down to 16 kHz mono int16 and hands the bytes on.
    private func emit(_ buffer: AVAudioPCMBuffer, as outputFormat: AVAudioFormat) {
        guard let converter, let onPCM else { return }

        let ratio = outputFormat.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 1024
        guard let output = AVAudioPCMBuffer(pcmFormat: outputFormat, frameCapacity: capacity) else { return }

        var consumed = false
        var conversionError: NSError?
        converter.convert(to: output, error: &conversionError) { _, status in
            if consumed {
                status.pointee = .noDataNow
                return nil
            }
            consumed = true
            status.pointee = .haveData
            return buffer
        }

        guard conversionError == nil,
              output.frameLength > 0,
              let channelData = output.int16ChannelData
        else { return }

        let byteCount = Int(output.frameLength) * MemoryLayout<Int16>.size
        onPCM(Data(bytes: channelData[0], count: byteCount))
    }
}
