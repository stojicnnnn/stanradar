"""Sources that are on the monitoring list but still need an adapter.

Each class records exactly what was found when the portal was probed (2026-10), so the
remaining work is implementation rather than investigation. They are registered on purpose:
putting one in `config.yaml` fails loudly at startup instead of quietly monitoring nothing.
"""

from __future__ import annotations

from .base import NotImplementedSource


class CityExpert(NotImplementedSource):
    name = "cityexpert"
    label = "cityexpert.rs"
    reason = (
        "single-page app; the category URL returns a ~4 KB shell with no listings. It has a "
        "JSON search API at POST https://cityexpert.rs/api/Search/ but the request shape "
        "changed - the documented {ptId, cityId, rentOrSale, ...} body now returns 400. The "
        "current shape has to be read off the site's own JS bundle or network traffic."
    )
