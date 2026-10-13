# Third-party pieces

Everything here is MIT unless it is listed below. These keep their own licenses.

| What | Where | License |
| :--- | :--- | :--- |
| Humanizer skill, by Siqi Chen | `claude/skills/humanizer/` | MIT, in that folder |
| Fonts for the design canvas | `claude/checklists/design/canvas-fonts/` | SIL Open Font License, beside each font |
| SpeexDSP, for the cube's echo cancellation | `voice/cube/lib/libspeexdsp.so.1` | BSD 3-Clause, Xiph.Org Foundation and contributors |
| markdown-it, DOMPurify, Chart.js, highlight.js | `voice/popup/vendor/` | MIT, Apache-2.0 or BSD, each `LICENSE.*` beside it |
| JetBrains Mono, Orbitron, Rajdhani | `voice/popup/vendor/fonts/` | SIL Open Font License, in `voice/popup/vendor/` |

Downloaded at install time and not stored here:

- The speech recognition, voice and speaker models from
  [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) releases, under the licenses
  their releases name.
- [pymicro-wakeword](https://github.com/OHF-Voice/pymicro-wakeword) (Apache-2.0),
  which runs the "Sunday" wake word model in `voice/popup/ear/wake/`.
- The marketing skills by Corey Haines,
  [coreyhaines31/marketingskills](https://github.com/coreyhaines31/marketingskills),
  if you install them.
