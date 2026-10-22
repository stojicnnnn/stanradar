"""Source registry."""

from __future__ import annotations

from ..config import Config
from .base import ClientPool, Source
from .fourzida import FourZida
from .halooglasi import HaloOglasi
from .kupujemprodajem import KupujemProdajem
from .nekretnine import Nekretnine
from .oglasi import Oglasi
from .pending import CityExpert

REGISTRY: dict[str, type[Source]] = {
    FourZida.name: FourZida,
    HaloOglasi.name: HaloOglasi,
    Nekretnine.name: Nekretnine,
    Oglasi.name: Oglasi,
    KupujemProdajem.name: KupujemProdajem,
    CityExpert.name: CityExpert,
}


def build(names: list[str], pool: ClientPool, cfg: Config, pages: int = 1) -> list[Source]:
    """Instantiate the configured sources, rejecting unknown or unimplemented ones."""
    sources: list[Source] = []
    for name in names:
        cls = REGISTRY.get(name)
        if cls is None:
            raise ValueError(f"unknown source {name!r}; available: {', '.join(sorted(REGISTRY))}")
        if not cls.implemented:
            raise ValueError(f"source {name!r} has no adapter yet: {cls.reason}")

        client = pool.for_source(cls)
        # Fetch the union of what every profile asks for, then let each profile filter.
        types = sorted(cfg.property_types)
        if cls is FourZida:
            # 4zida is the only source that can pre-filter server-side, which keeps its
            # response small. The ceiling is the most generous profile's, so a cheap
            # profile never hides listings a more expensive one wants.
            caps = [p.criteria.price_eur.max for p in cfg.profiles if p.criteria.price_eur.max]
            floors = [p.criteria.area_m2.min for p in cfg.profiles if p.criteria.area_m2.min]
            sources.append(
                FourZida(
                    client,
                    price_to=int(max(caps)) if caps else None,
                    m2_from=int(min(floors)) if floors else None,
                    property_types=types,
                    pages=pages,
                )
            )
        else:
            sources.append(cls(client, property_types=types))
    return sources


__all__ = ["REGISTRY", "ClientPool", "Source", "build"]
