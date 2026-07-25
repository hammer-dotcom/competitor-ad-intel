"""The label set. Deliberately its own module with zero dependencies.

Both consumers import from here: the cron pipeline (src/classify.py, which calls the
Anthropic API) and the MCP server (src/mcp_server.py, where the connected Claude does the
labelling and no SDK is needed). Keeping the taxonomy dependency-free is what lets the MCP
server run without an Anthropic key.

Rule of thumb when editing: if two values could plausibly describe the same ad, merge them.
Overlapping enums produce inconsistent labelling, and inconsistent labels can't be trended.
"""

ANGLES = [
    "pain/problem-agitation", "outcome/ROI", "speed-to-value", "cost-saving",
    "compliance/security", "social-proof/customer-story", "product-feature",
    "competitor-switch", "authority/thought-leadership", "event/webinar",
    "fear-of-missing-out", "other",
]

OFFERS = [
    "book-a-demo", "free-trial", "pricing/quote", "gated-report", "webinar",
    "template/tool", "newsletter", "consultation/audit", "none/brand", "other",
]

FUNNEL = ["top", "middle", "bottom"]
