"""Evaluator-only reference for the URL-shortener exercise.

Do not add this module to interviewer start/session payloads. The interviewer
skill may consult it only after the candidate transcript is complete.
"""

REFERENCE_ARCHITECTURE = {
    "request_paths": [
        "Create: validate request and idempotency key, allocate a collision-safe short code, persist the mapping, then return the short URL.",
        "Redirect: resolve short code from cache, then a read replica, then the primary store on a miss; cache successful mappings.",
        "Analytics: emit redirect events asynchronously to a durable queue and aggregate them into an analytics store, keeping the redirect path independent of analytics availability.",
    ],
    "acceptance_criteria": [
        "Short-code uniqueness is guaranteed by an authoritative uniqueness constraint or collision retry, not by assuming a hash cannot collide.",
        "The service meets the stated redirect latency and availability targets at the stated peak read load; cache and horizontal stateless service scaling are justified against that load.",
        "A newly created mapping is visible within the five-second scenario target; a cache miss or replica lag is not reported as a confirmed missing link. The candidate may choose primary fallback, read-after-write routing, or another justified mechanism.",
        "A confirmed missing code is distinguishable from a dependency timeout. Negative cache entries expire quickly enough to respect the visibility target.",
        "Link expiration is an explicit product/data-retention decision. If expiration is supported, expiry is enforced on redirect and cleanup does not break correctness.",
        "Analytics writes are asynchronous and do not add a synchronous database write to every redirect unless the candidate explicitly justifies that trade-off.",
        "The design states its consistency, failure, and retry behavior, including protection against retry storms or hot keys where relevant.",
    ],
    "illustrative_components": [
        "L7 ingress with rate limiting",
        "stateless link creation and redirect services",
        "distributed cache for hot mappings",
        "primary relational or key-value mapping store with read replicas",
        "durable event queue and asynchronous analytics consumers",
        "analytics-optimized storage for aggregate queries",
    ],
    "evaluation_note": "This is one acceptable shape, not a required vendor or exact topology. Score reasoning and trade-offs rather than matching component names.",
}
