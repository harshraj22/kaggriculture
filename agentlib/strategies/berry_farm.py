"""BerryFarm — `MelonFarm` with strawberry as the premium crop.

Same engine, same three overrides; only the crop and its sizing differ. The case
for it is that melon and strawberry sit at opposite extremes of the two things
that matter, and melon wins the one that matters less:

    crop         units/tile-day   season drain   shops buying it
    MELON                  0.55             30                 0
    STRAWBERRY             0.24            406                 3.9

Melon grows more than twice as fast per tile and is worth more per unit, but the
world only absorbs **30 melons a season** — `melon_farm` sells 110 and takes the
price from 266 to 62. Strawberry grows slowly and is bought by ~4 shops, which
drain it continuously; measured over an episode its price ROSE from 155 to 264
while melon's collapsed.

Strawberry is an ongoing crop and the docs oversell it: it yields exactly four
times, at ages 10, 12, 14 and 16, then dies. That is 4 units per 17 tile-days,
so a tile turns over barely more than once in a 30-day season and the plot has
to be large to matter.
"""

from .melon_farm import MelonFarm


class BerryFarm(MelonFarm):
    name = "berry_farm"

    PREMIUM = "STRAWBERRY"

    #: Large, because the crop is slow and the market is deep. Sized against the
    #: 406-unit drain rather than against the farm.
    PREMIUM_TILES = 20

    #: A strawberry sown with fewer days left cannot reach `first_yield_day` 10.
    PREMIUM_LEAD_DAYS = 12

    #: Barely throttled: four shops keep this market drained, so unlike melon the
    #: glut does not persist.
    PREMIUM_FLOOR_RATIO = 0.9
