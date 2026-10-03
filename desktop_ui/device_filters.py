"""Combined device filters. Capability evidence is never inferred from model names."""


def matches_device(record, *, query="", status="", model="", firmware="", led="", errors=False):
    text = " ".join(str(record.get(key) or "") for key in
                    ("IP", "Model", "Firmware", "FirmwareVersion", "Worker", "Pool", "Error")).casefold()
    if not all(word in text for word in query.casefold().split()):
        return False
    if status and (not bool(record.get("Stale")) if status == "stale" else record.get("Status") != status):
        return False
    if model and record.get("Model") != model:
        return False
    if firmware and record.get("Firmware") != firmware:
        return False
    state = record.get("IdentifyEnabled")
    if led and not {"on": state is True, "off": state is False, "unknown": state is not True and state is not False}.get(led, False):
        return False
    if errors and not str(record.get("Error") or "").strip():
        return False
    return True
