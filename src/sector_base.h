/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file sector_base.h Base class for sectors: regions of the map where companies need building rights. */

#ifndef SECTOR_BASE_H
#define SECTOR_BASE_H

#include "sector_type.h"
#include "company_type.h"
#include "money_type.h"
#include "tile_type.h"
#include "town_type.h"
#include "core/pool_type.hpp"
#include <string>

typedef Pool<Sector, SectorID, 16> SectorPool;
extern SectorPool _sector_pool;

/** A region of the map. Companies can only build in sectors in which they bought building rights. */
struct Sector : SectorPool::PoolItem<&_sector_pool> {
	std::string name{};            ///< Custom name of the sector, empty for an automatic name.
	Money custom_price = 0;        ///< Custom base price of the building rights (before inflation), 0 for a price based on the number of tiles.
	bool no_first_pick = false;    ///< The sector cannot be chosen as the free first sector of a company.
	CompanyMask rights{};          ///< Companies which own building rights in this sector.
	TownID town = TownID::Invalid(); ///< Town the automatic name is taken from.

	/* Cached, not saved. */
	uint32_t tile_count = 0;       ///< Number of tiles in the sector.
	TileIndex centre = INVALID_TILE; ///< Tile of the sector nearest to its centre of mass.

	Sector(SectorID index) : PoolItemBase(index) {}
	~Sector();

	Money GetBasePrice() const;
	Money GetPrice() const;
	Money GetPriceFor(CompanyID company) const;
	bool HasRights(CompanyID company) const { return this->rights.Test(company); }
};

#endif /* SECTOR_BASE_H */
