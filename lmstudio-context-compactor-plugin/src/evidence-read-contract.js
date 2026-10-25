"use strict";
// Entire JSON response budget; body offsets remain UTF-16 code units. The
// maximum lets one call return a typical archived source body; the shared
// read-result broker still lowers each request to the input room it has.
const ARCHIVE_READ_CONTRACT = Object.freeze({ minimumEnvelopeChars: 2048,
  maximumEnvelopeChars: 24576, defaultEnvelopeChars: 4096, minimumBodyChars: 128 });
module.exports = { ARCHIVE_READ_CONTRACT };
