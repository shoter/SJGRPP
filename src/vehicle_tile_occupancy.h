/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file vehicle_tile_occupancy.h One bit per tile telling whether the vehicle tile hash of a vehicle type has an entry for the tile. */

#ifndef VEHICLE_TILE_OCCUPANCY_H
#define VEHICLE_TILE_OCCUPANCY_H

#include "tile_type.h"
#include <vector>

/**
 * Occupancy bitmap of one vehicle type's tile hash: the bit of a tile is set while the hash has a key for that tile.
 * It is a pure cache, so lookups can skip the hash probe (a likely cache miss) for empty tiles.
 * Tiles outside the bitmap are never marked and always report "may be occupied", so a bitmap of the wrong size
 * is slower but never wrong.
 */
class VehicleTileOccupancy {
	std::vector<uint64_t> words;
	uint32_t tiles = 0;

public:
	/**
	 * Clear all bits and size the bitmap for a map.
	 * @param tiles Number of tiles of the map.
	 */
	void Reset(uint32_t tiles)
	{
		this->tiles = tiles;
		this->words.assign((static_cast<size_t>(tiles) + 63) / 64, 0);
	}

	/** @return Number of tiles covered by the bitmap. */
	uint32_t Size() const { return this->tiles; }

	/** Mark a tile as having a hash entry. */
	inline void Set(TileIndex tile)
	{
		const uint32_t i = tile.base();
		if (i < this->tiles) this->words[i >> 6] |= static_cast<uint64_t>(1) << (i & 63);
	}

	/** Mark a tile as having no hash entry. */
	inline void Clear(TileIndex tile)
	{
		const uint32_t i = tile.base();
		if (i < this->tiles) this->words[i >> 6] &= ~(static_cast<uint64_t>(1) << (i & 63));
	}

	/**
	 * Whether the hash may have an entry for a tile.
	 * @return false only when the tile is covered by the bitmap and its bit is clear.
	 */
	inline bool MayBeOccupied(TileIndex tile) const
	{
		const uint32_t i = tile.base();
		if (i >= this->tiles) return true;
		return (this->words[i >> 6] >> (i & 63)) & 1;
	}

	bool operator==(const VehicleTileOccupancy &other) const = default;
};

#endif /* VEHICLE_TILE_OCCUPANCY_H */
