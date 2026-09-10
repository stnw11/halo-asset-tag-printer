from dataclasses import dataclass, field


@dataclass(frozen=True)
class Asset:
    id: int
    qty: int
    asset_tag: str
    raw: dict = field(default_factory=dict)
