# Chrome Web Store notes — 0.4.1

Response capture now shows a 15-second countdown and offers clear recovery when a
chat page does not expose a stable reply element. You can check the same post-send
snapshot again without resending, re-detect the response element, or provide the
latest bot response manually without restarting the assessment.

Capture also handles more chat widgets that append replies as sibling message
envelopes and rejects overly broad response containers containing the input or Send
control. FrameFuzz now turns off automatically when an incompatible objective is
selected, and its toggle layout has been corrected.

No new Chrome permissions, remote code, or stored credentials are introduced.
