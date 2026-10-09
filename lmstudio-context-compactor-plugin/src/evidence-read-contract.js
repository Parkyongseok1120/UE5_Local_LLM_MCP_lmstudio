"use strict";
// Entire JSON response budget; body offsets remain UTF-16 code units.
const ARCHIVE_READ_CONTRACT = Object.freeze({ minimumEnvelopeChars: 2048,
  maximumEnvelopeChars: 8192, defaultEnvelopeChars: 4096, minimumBodyChars: 128 });
module.exports = { ARCHIVE_READ_CONTRACT };
