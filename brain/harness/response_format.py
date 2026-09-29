"""Small, streaming parser for an optional display-only expression prefix."""
EXPRESSIONS = frozenset({
    "neutral", "happy", "sad", "alert", "bored", "dead_inside", "error",
    "smug", "curious", "sleepy", "angry", "love", "suspicious", "surprised",
    "panic", "listening", "thinking", "mischief", "proud", "embarrassed",
})


class ResponsePrefixFilter:
    """Consume one leading [[face:name]] marker without delaying plain speech."""
    marker = "[[face:"

    def __init__(self):
        self.buffer = ""
        self.decided = False
        self.discarding = False
        self.trim_after_prefix = False

    def feed(self, delta: str) -> tuple[str, str | None]:
        if self.decided:
            if self.trim_after_prefix:
                delta = delta.lstrip()
                self.trim_after_prefix = not bool(delta)
            return delta, None
        self.buffer += delta
        if not self.discarding:
            candidate = self.buffer.lstrip()
            if self.marker.startswith(candidate):
                self.buffer = self.buffer[-64:]
                return "", None
            if candidate.startswith(self.marker):
                self.buffer = candidate
        if self.marker.startswith(self.buffer):
            return "", None
        if not self.buffer.startswith(self.marker) and not self.discarding:
            self.decided = True
            text, self.buffer = self.buffer, ""
            return text, None
        end = self.buffer.find("]]", len(self.marker) if not self.discarding else 0)
        if end < 0:
            if len(self.buffer) > 64:
                self.discarding = True
                self.buffer = self.buffer[-1:]
            return "", None
        expression = self.buffer[len(self.marker):end] if not self.discarding else ""
        text = self.buffer[end + 2:].lstrip()
        self.buffer = ""
        self.decided = True
        self.trim_after_prefix = not bool(text)
        return text, expression if expression in EXPRESSIONS else None

    def finish(self) -> str:
        text = self.buffer
        self.buffer = ""
        self.decided = True
        # An incomplete control marker must not reach speech synthesis.
        return "" if self.discarding or text.startswith(self.marker) or self.marker.startswith(text) else text


def strip_expression_prefix(text: str) -> tuple[str, str | None]:
    stream = ResponsePrefixFilter()
    visible, expression = stream.feed(text)
    return visible + stream.finish(), expression
