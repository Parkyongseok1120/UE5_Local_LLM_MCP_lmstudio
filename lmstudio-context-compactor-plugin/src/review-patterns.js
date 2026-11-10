"use strict";

// Shared by progress/engine anchoring and the execution's unverified notes.
const REJECTED = /기각|철회|버그\s*아님|결함\s*아님|문제\s*없|정상|\bnot\s+a\s+(?:bug|defect)\b|\bno\s+issue\b|\brejected\b|\bretract\w*\b|\bcorrect\b|\bis\s+fine\b/iu;
const FINDING = /\bunused\b|\bnever\s+(?:used|read|called|played)\b|\bdead\s+(?:code|field|data)\b|\bwrite-only\b|\bno\s+(?:caller|consumer)\b|미사용|사용처\s*없|호출\s*없|쓰기만|\bbug\b|\bdefect\b|버그|결함|\bP[0-3]\b/iu;
const REVIEW_REGISTER_HEADER = "Runtime review register (assistant working notes, not verified facts)";
const REVIEW_REGISTER_GUIDANCE = "Before reporting, include each found row or state why it is excluded; do not re-verify cleared rows. A ↳ line is that judgment's evidence (location, sha, search scope, code): report from it, and re-read its file only when the sha changed or new evidence contradicts it.";
// The frame of the delivered report shown to a review round. The output
// filter strips both lines when a model copies the frame into its answer.
const DELIVERED_REPORT_HEADER = "[Delivered report — already shown to the user; write only what follows it]";
const DELIVERED_REPORT_END = "[End delivered report]";
module.exports = { REJECTED, FINDING, REVIEW_REGISTER_HEADER, REVIEW_REGISTER_GUIDANCE, DELIVERED_REPORT_HEADER, DELIVERED_REPORT_END };
