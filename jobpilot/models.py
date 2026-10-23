from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Job:
    id: str                      # "ba:10001-...", "personio:ftt:123", "manual:ezt"
    source: str                  # ba | personio | greenhouse | recruitee | lever | smartrecruiters | ashby | workable | manual
    company: str
    title: str
    source_url: str = ""
    apply_url: str = ""          # портал/форма работодателя, если есть
    external_url: str = ""       # объявление размещено у третьей стороны
    location: str = ""
    lat: float | None = None
    lon: float | None = None
    distance_km: float | None = None   # от точки поиска (BA)
    homeoffice: bool = False
    posted: str = ""
    deadline: str = ""
    description: str = ""
    email_hint: str = ""         # e-mail, известный заранее (ручные лиды)
    note: str = ""               # дополнение к письму (ручные лиды)
    needs_detail: bool = False   # описание ещё не загружено (BA)
    raw: dict = field(default_factory=dict)
