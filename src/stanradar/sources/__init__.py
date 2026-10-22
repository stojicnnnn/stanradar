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


def build(names: list[str], pool: ClientPool, cfg: Config) -> list[Source]:
    """Instantiate the configured sources, rejecting unknown or unimplemented ones."""
    sources: list[Source] = []
    for name in names:
        cls = REGISTRY.get(name)
        if cls is None:
            raise ValueError(f"unknown source {name!r}; available: {', '.join(sorted(REGISTRY))}")
        if not cls.implemented:
            raise ValueError(f"source {name!r} has no adapter yet: {cls.reason}")

        client = pool.for_source(cls)
        if cls is FourZida:
            # 4zida is the only source that can pre-filter server-side, which keeps its
            # response small. Everything precise still happens locally.
            sources.append(
                FourZida(
                    client,
                    price_to=int(cfg.criteria.price_eur.max)
                    if cfg.criteria.price_eur.max
                    else None,
                    m2_from=int(cfg.criteria.area_m2.min) if cfg.criteria.area_m2.min else None,
                )
            )
        else:
            sources.append(cls(client))
    return sources


__all__ = ["REGISTRY", "ClientPool", "Source", "build"]
