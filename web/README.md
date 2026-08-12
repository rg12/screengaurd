# iPhone web app — no Mac required

`index.html` is the whole app: microphone → Deepgram (`nova-3`, streaming) → transcript
→ Claude (`claude-sonnet-5`, streaming) → suggested reply. One self-contained file, no
build step, no dependencies.

## Why this instead of the Swift app

Building the native iOS app in `../ios/` requires Xcode, which only runs on macOS, plus
an Apple Developer account to install on a device. This runs on your iPhone today with
neither.

## Getting it onto your iPhone

The microphone requires a **secure context**, so it must be served over HTTPS. Opening
the file directly (`file://`) or over plain `http://` from your PC **will not work** —
Safari blocks mic access.

Pick any one of these:

**Netlify Drop (fastest, no account needed to start)**
1. Go to <https://app.netlify.com/drop>
2. Drag the `web` folder onto the page
3. Open the URL it gives you on your iPhone

**GitHub Pages**
1. Push this folder to a repo
2. Settings → Pages → deploy from branch, root `/web`
3. Open the `github.io` URL on your iPhone

**Cloudflare Pages / Vercel** — same idea; upload the folder, open the URL.

### Add it to your home screen

In Safari: **Share → Add to Home Screen**. It then opens full-screen with no browser
chrome, like an app. (This must be done in Safari — Chrome on iOS cannot install it.)

## First run

1. Tap **Keys** and paste your Deepgram and Anthropic API keys. They are saved in this
   browser's local storage on this device only.
2. Choose an audio source.
3. Tap **Start listening** and allow microphone access when Safari asks.

## Audio sources

| Mode | What it does |
| --- | --- |
| **Microphone** | Normal capture with echo cancellation and noise suppression on. Best for in-person conversations. |
| **Speakerphone** | Put the call on speaker so the mic hears both sides. Turns echo cancellation **off** on purpose — it is designed to subtract speaker output from the mic, which would delete the far-end voice you want. |

There is no direct capture of call audio. iOS exposes no API for it to any app, native
or web, so speakerphone is the only route to the other person's voice.

## Real limitations

- **Foreground only.** iOS suspends audio capture when you leave the page or lock the
  screen. The app tells you when this happens. There is no background-transcription
  workaround for web apps on iOS.
- **Keys live in local storage**, readable by anyone with the unlocked device. Fine for
  personal use; if you ever share this, put a server in front of the APIs instead of
  shipping keys to the browser.
- **Costs money.** Deepgram bills per minute of audio and Anthropic per token, against
  your own accounts.
- Needs a live network connection — everything is streamed to hosted APIs.

## Legal

Recording a conversation requires consent from everyone involved in many jurisdictions.
