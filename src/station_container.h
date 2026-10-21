/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file station_container.h Types related to station containers. */

#ifndef STATION_CONTAINER_H
#define STATION_CONTAINER_H

#include "station_type.h"
#include "tilearea_type.h"
#include "3rdparty/cpp-btree/btree_set.h"
#include "3rdparty/svector/svector.h"
#include <span>

struct StationCompare {
	bool operator() (const Station *lhs, const Station *rhs) const;
};

/** List of stations */
typedef btree::btree_set<Station *, StationCompare> StationList;

/** List of stations sorted by station index, without duplicates. */
using StationFinderList = ankerl::svector<Station *, 8>;

/**
 * Structure contains cached list of stations nearby. The list
 * is created upon first call to GetStations()
 */
class StationFinder : TileArea {
	StationFinderList stations; ///< List of stations nearby, sorted by station index
public:
	/**
	 * Constructs StationFinder
	 * @param area the area to search from
	 */
	StationFinder(const TileArea &area) : TileArea(area) {}
	const StationFinderList &GetStations();
};

/**
 * Entry of a town's nearby station cache (Town::GetStationsNearCache).
 * Stores a station together with the area of its catchment bitmap, so that a house tile can be
 * tested against the area without touching the station or its bitmap.
 */
struct StationNearCacheEntry {
	uint32_t x; ///< X of the north corner of the catchment area.
	uint32_t y; ///< Y of the north corner of the catchment area.
	uint32_t w; ///< Width of the catchment area.
	uint32_t h; ///< Height of the catchment area.
	Station *st; ///< The station.

	/**
	 * Test whether a tile is inside the catchment area (not necessarily in the catchment itself).
	 * @param tx Tile X.
	 * @param ty Tile Y.
	 * @return true if the tile is within the area.
	 */
	inline bool AreaContains(uint32_t tx, uint32_t ty) const
	{
		/* Unsigned wrap-around makes coordinates below x/y fail the test too. */
		return (tx - this->x) < this->w && (ty - this->y) < this->h;
	}

	bool operator==(const StationNearCacheEntry &other) const = default;
};

/**
 * Incremented whenever the catchment area (not only the covered tiles) of any station changes.
 * Nearby station caches built at an older value are out of date, even if their station list did not change.
 */
extern uint32_t _station_catchment_area_epoch;

/**
 * Collect stations from a nearby station cache whose catchment contains a tile.
 * The order of the cache is kept, so a cache sorted by station index gives a result sorted by station index.
 * @param cache The cache entries.
 * @param tx Tile X.
 * @param ty Tile Y.
 * @param in_catchment Function taking a Station * and returning whether the tile is in its catchment; only called when the area contains the tile.
 * @param out Container to append matching stations to.
 */
template <typename F, typename Out>
inline void FilterStationNearCache(std::span<const StationNearCacheEntry> cache, uint32_t tx, uint32_t ty, F in_catchment, Out &out)
{
	for (const StationNearCacheEntry &entry : cache) {
		if (entry.AreaContains(tx, ty) && in_catchment(entry.st)) out.push_back(entry.st);
	}
}

using StationIDVector = ankerl::svector<StationID, 1>;

#endif /* STATION_CONTAINER_H */
