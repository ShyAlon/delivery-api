# Socket limits must survive a provider boundary

The SDK configured model timeouts, but its broker sent only exact JSON bytes. The parent rebuilt HTTP requests with fixed 3600-second clients, so SDK timeout extensions did not reach the real socket. Both interview and generator use ParentProviderHTTP; its trusted parent policy now applies the existing VMODEL_LLM_CALL_TIMEOUT_S (default 600) and streaming VMODEL_LLM_STALL_TIMEOUT_S (default 180) as HTTP phase limits. Invalid limits refuse before sending. Child headers/extensions cannot alter the policy.

The fail-first request fixture saw 3600 instead of 23 seconds before the fix. A real local HTTP server then holds the response; the parent times out, retains uncertain accounting, and a second logical dispatch does not send again. No real provider was used.

HTTP phase limits are not an absolute wall-clock bound, and the child adaptive watchdog is not transmitted per call. That larger contract remains separate; do not claim this makes active socket work disappear when a child future times out.
