"""Narrow compatibility for legacy stock responses; never rewrite JSON strings."""
import json
import re


def repair_antminer_stats(text):
    """Recover separators in complete Stock Z11/Z15/Z15e STATS replies.

    Earlier releases used global regex substitutions for this firmware defect.
    Only edit locations identified by the JSON decoder, then require a known model
    in the result. Truncated packets and other firmware families stay invalid.
    """
    candidate = text
    for _ in range(17):
        try:
            value = json.loads(candidate)
            break
        except json.JSONDecodeError as exc:
            tail = candidate[exc.pos:]
            if exc.msg == "Expecting ',' delimiter" and re.match(r'"(?:[^"\\]|\\.)*"\s*:', tail):
                candidate = candidate[:exc.pos] + "," + tail
            elif (exc.msg == "Expecting ',' delimiter" and tail.startswith("{")
                  and candidate[:exc.pos].rstrip().endswith("}")):
                # Stock Z15 2020: adjacent STATS array objects are sent as }{.
                candidate = candidate[:exc.pos] + "," + tail
            elif (exc.msg.startswith("Illegal trailing comma") and
                  re.match(r",\s*[}\]]", tail)):
                # Python 3.14 reports the comma itself; older decoders point
                # at the following closing bracket (handled below).
                candidate = candidate[:exc.pos] + tail[1:]
            elif (exc.msg in {"Expecting property name enclosed in double quotes", "Expecting value"}
                  and tail[:1] in ("}", "]") and candidate[:exc.pos].rstrip().endswith(",")):
                prefix = candidate[:exc.pos].rstrip()
                candidate = prefix[:-1] + tail
            else:
                raise ValueError("Unsupported legacy JSON defect") from None
    else:
        raise ValueError("Too many legacy JSON defects")
    stats = value.get("STATS") if isinstance(value, dict) else None
    if not isinstance(stats, list) or not any(
        isinstance(item, dict) and re.fullmatch(r"Antminer\s+(?:Z11|Z15(?:e|\+|\s+Pro)?)", str(item.get("Type", "")), re.I)
        for item in stats
    ):
        raise ValueError("Legacy repair requires a known stock Z-series STATS identity")
    return candidate
