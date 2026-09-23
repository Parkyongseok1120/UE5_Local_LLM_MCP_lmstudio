# Gate 0 baseline

Start: `3c4375fd3041da16b4e6af721c17fa2c230d2f50` (`리팩토링 준비`). Origin matches the requested repository. Initial working tree clean.

Instructions: ZIP extracted outside the repository; START_HERE read first, then 00 through 05 in order, followed by package manifest/snapshot. No prior SOL/Luna work used.

`npm run build`: exit 0. `npm test`: exit 0, 307 passed, 0 failed, 0 skipped (49.96 seconds). Logs and pre-build source/dist SHA-256 inventory are adjacent to this file. Node v24.17.0, npm 11.13.0. Tests run in `lmstudio-context-compactor-plugin`.

PredictionLoop git blob: 187252 bytes; Windows working file: 191003 bytes (line-ending difference). Structural comparisons use UTF-8 LF bytes.

Gate order: 0 baseline, 1 extraction, 2 phase, 3 broker/ratchet, 4 evidence, 5 capability, 6 recovery/delivery, 7 orchestration, 8 regression/runtime.

Rollback: original SHA; no user edits present. Changes remain reviewable in the working tree.
