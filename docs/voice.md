# Sunday, the voice

The voice server runs on the host. Speech recognition (NVIDIA Parakeet) and the
voice (Kokoro, or Piper) are local models run through sherpa-onnx; ElevenLabs is an
optional second voice. Claude Code, signed in on the host, is the brain. The full
behaviour, from the speech gate to talking over it, is in
[`voice/README.md`](../voice/README.md).

## Putting it on the host

With `host.ssh`, `host.user` and `host.home` set in your profile:

```bash
~/.mtoa/bin/mtoa voice deploy
```

That copies the server, makes its virtualenv, downloads the models (about 1 GB),
installs the systemd units for the account the profile names, and restarts it. Then:

```bash
ssh <host> 'curl -s localhost:8794/health'
```

For ElevenLabs, keep the key in your credential store as `elevenlabs-api-key`. Without
it the server speaks with Kokoro, which needs nothing.

## Ways to talk to it

- **Any computer with a microphone.** `~/.mtoa/bin/mtoa voice ear` (after
  `pip install numpy sounddevice websockets pymicro-wakeword`). It finds the server
  from `host.lan_ip` and `host.voice_port`. With no echo cancellation it does not
  listen while it talks; a speakerphone that cancels its own echo can set
  `SUNDAY_EAR_DUPLEX=1`.
- **The Mac app** in `voice/popup/`: a panel of what it hears and says, a canvas
  for answers too long to speak, and an ear on the Mac's own microphone with echo
  cancellation. Build it with `npm install && npm run dist:mac`; the build reads
  your profile for the host's address.
- **The speaker cube** in `voice/cube/`: a small Linux speaker running the wake word
  on the device. `voice/deploy/deploy-cube.sh` installs it over `ssh cube`.

## The wake word

The model in `voice/popup/ear/wake/` is trained on "Sunday". Calling the assistant
something else is a profile change (`assistant.name`), but answering to another
word needs a microWakeWord model trained on it, placed beside that one.
