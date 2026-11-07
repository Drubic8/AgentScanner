"""Provider settings contain public configuration only, never an API credential."""
from collections.abc import Mapping
from dataclasses import dataclass
import re


_FOLDER = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}(?:/[A-Za-z0-9._-]{1,32})?\Z")
_CHATGPT_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


@dataclass(frozen=True)
class AnalysisConfig:
    provider: str = "yandex"
    folder_id: str = ""
    model: str = "yandexgpt-5.1"
    timeout_seconds: int = 45
    max_output_tokens: int = 1600

    def __post_init__(self):
        if self.provider not in ("yandex", "chatgpt"):
            raise ValueError("Unsupported analysis provider")
        if not isinstance(self.folder_id, str) or (self.folder_id and not _FOLDER.fullmatch(self.folder_id)):
            raise ValueError("Invalid AI Studio folder ID")
        if not isinstance(self.model, str) or not (
                self.provider == 'chatgpt' and (self.model == '' or _CHATGPT_MODEL.fullmatch(self.model))
                or self.provider == 'yandex' and _MODEL.fullmatch(self.model)):
            raise ValueError("Invalid AI Studio model name")
        for name, minimum, maximum in (("timeout_seconds", 5, 120), ("max_output_tokens", 64, 4096)):
            value = getattr(self, name)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError("Invalid analysis request limit")

    @classmethod
    def from_mapping(cls, mapping):
        if not isinstance(mapping, Mapping):
            return cls()
        defaults = cls(provider=mapping.get('provider', 'yandex'),
                       model='' if mapping.get('provider') == 'chatgpt' else 'yandexgpt-5.1')
        return cls(**{name: mapping.get(name, getattr(defaults, name)) for name in cls.__dataclass_fields__})

    def to_mapping(self):
        return {name: getattr(self, name) for name in self.__dataclass_fields__}

    @property
    def model_uri(self):
        if self.provider == 'chatgpt':
            if not self.model:
                raise ValueError('Select an available ChatGPT model')
            return self.model
        if not self.folder_id:
            raise ValueError("AI Studio folder ID is required")
        return f"gpt://{self.folder_id}/{self.model}"
