import SwiftUI

struct ContentView: View {
    @StateObject private var model = ConversationViewModel()
    @State private var showingSettings = false

    var body: some View {
        NavigationStack {
            VStack(spacing: 12) {
                sourcePicker
                statusLine
                transcriptPane
                replyPane
                listenButton
            }
            .padding()
            .navigationTitle("Conversation Assistant")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Clear", action: model.clear)
                        .disabled(model.transcript.isEmpty && model.replies.isEmpty)
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showingSettings = true } label: { Image(systemName: "key") }
                }
            }
            .sheet(isPresented: $showingSettings) { SettingsView() }
            .alert(
                "Something went wrong",
                isPresented: .init(
                    get: { model.errorMessage != nil },
                    set: { if !$0 { model.errorMessage = nil } }
                ),
                actions: { Button("OK", role: .cancel) { model.errorMessage = nil } },
                message: { Text(model.errorMessage ?? "") }
            )
        }
    }

    private var sourcePicker: some View {
        VStack(alignment: .leading, spacing: 4) {
            Picker("Audio source", selection: $model.audioSource) {
                ForEach(AudioSource.allCases) { Text($0.rawValue).tag($0) }
            }
            .pickerStyle(.segmented)
            .disabled(model.isListening)

            Text(model.audioSource.explanation)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    private var statusLine: some View {
        HStack(spacing: 8) {
            Circle()
                .fill(model.isListening ? .green : .secondary)
                .frame(width: 8, height: 8)
            Text(model.status)
                .font(.caption)
                .foregroundStyle(.secondary)
            Spacer()
        }
    }

    private var transcriptPane: some View {
        pane(title: "Live transcript") {
            if !model.liveCaption.isEmpty {
                Text(model.liveCaption)
                    .foregroundStyle(.secondary)
                    .italic()
            }
            ForEach(Array(model.transcript.enumerated().reversed()), id: \.offset) { _, line in
                Text(line)
            }
        }
    }

    private var replyPane: some View {
        pane(title: "Suggested reply") {
            ForEach(Array(model.replies.enumerated().reversed()), id: \.offset) { _, reply in
                Text(reply)
                    .textSelection(.enabled)
                    .padding(8)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color.accentColor.opacity(0.1), in: RoundedRectangle(cornerRadius: 8))
            }
        }
    }

    private func pane<Content: View>(
        title: String,
        @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title)
                .font(.subheadline.weight(.semibold))
            ScrollView {
                VStack(alignment: .leading, spacing: 8) {
                    content()
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(maxHeight: .infinity)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var listenButton: some View {
        Button(action: model.toggleListening) {
            Text(model.isListening ? "Stop listening" : "Start listening")
                .frame(maxWidth: .infinity)
                .padding(.vertical, 6)
        }
        .buttonStyle(.borderedProminent)
        .tint(model.isListening ? .red : .accentColor)
    }
}

struct SettingsView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var deepgramKey = ""
    @State private var anthropicKey = ""

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    SecureField("dg_…", text: $deepgramKey)
                } header: {
                    Text(KeychainStore.Key.deepgram.displayName)
                } footer: {
                    Text("Used for live speech-to-text.")
                }

                Section {
                    SecureField("sk-ant-…", text: $anthropicKey)
                } header: {
                    Text(KeychainStore.Key.anthropic.displayName)
                } footer: {
                    Text("Used to generate suggested replies. Keys are stored in the iOS keychain.")
                }
            }
            .navigationTitle("API keys")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        KeychainStore.save(deepgramKey, for: .deepgram)
                        KeychainStore.save(anthropicKey, for: .anthropic)
                        dismiss()
                    }
                }
            }
            .onAppear {
                deepgramKey = KeychainStore.read(.deepgram) ?? ""
                anthropicKey = KeychainStore.read(.anthropic) ?? ""
            }
        }
    }
}
