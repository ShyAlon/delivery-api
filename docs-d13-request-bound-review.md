# D13 request-bound review note (not run permission)

Status: accounting branch prepared; paid admission still blocked.

DeepSeek's response usage is authoritative for actual billing. The implementation
uses that fact for settlement. Admission is separate: a reservation is meaningful
as spend protection only when actual input/output charges cannot exceed it.

A conservative input estimate + explicit API max_tokens + pre-call balance read is
NOT a guaranteed reservation. The provider can tokenize chat/tool framing beyond
an offline estimate; three concurrent calls can all see the same balance. The
balance endpoint is a read, not an atomic provider spending hold. Overestimation
can be released after settlement, but underestimation has already spent money.

The owner's 128000 protective run ceiling is not the published model-wide maximum.
The current serializer/model-wide bound adapter cannot prove requests stay below
that owner limit. It refuses paid contact rather than treating an estimate as proof.

Possible design for owner review: use an explicitly labeled estimate (not verified
bound), enforce max_tokens, snapshot provider balance, and settle actual usage with
an overrun held for review. Residual risk: spend can exceed the reservation and
protective limit before actual usage arrives. This is NOT implemented/enabled.

Another possibility: reserve a clearly labeled conservative model-wide upper
ceiling that covers both decimal and binary interpretations of published "1M",
while treating 128000 as owner policy, not provider capacity. This avoids pretending
128k is provider-enforced but needs validation of the vendor maximum/version and
cannot claim exact input-policy enforcement by itself. NOT enabled.

No founder tokenizer-mechanics question is required. The admission block remains
until a sound bound is validated or the owner explicitly reviews and accepts an
estimate's residual underreservation risk as a design change.

Sources inspected:
https://api-docs.deepseek.com/quick_start/pricing/
https://api-docs.deepseek.com/api/create-chat-completion/
https://api-docs.deepseek.com/zh-cn/quick_start/token_usage
