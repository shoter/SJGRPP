/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file script_sector.hpp Everything to query sectors and buy building rights. */

#ifndef SCRIPT_SECTOR_HPP
#define SCRIPT_SECTOR_HPP

#include "script_company.hpp"
#include "script_error.hpp"
#include "../../sector_type.h"
#include <optional>

/**
 * Class that handles sectors: regions of the map in which a company can only
 *  build, demolish and terraform after buying building rights.
 * The first sector of a company is free, unless that sector does not allow it.
 * @api ai game
 */
class ScriptSector : public ScriptObject {
public:
	/**
	 * All sector related error messages.
	 */
	enum ErrorMessages {
		/** Base for sector related errors */
		ERR_SECTOR_BASE = ScriptError::ERR_CAT_SECTOR << ScriptError::ERR_CAT_BIT_SIZE,

		/** The company has no building rights in the sector of a tile it tried to build on */
		ERR_SECTOR_NO_BUILDING_RIGHTS,        // [STR_ERROR_SECTOR_NO_BUILDING_RIGHTS]

		/** The company already owns building rights in the sector */
		ERR_SECTOR_ALREADY_HAVE_RIGHTS,       // [STR_ERROR_SECTOR_ALREADY_HAVE_RIGHTS]
	};

	/**
	 * Check whether sectors are enabled, i.e. whether companies need building rights.
	 * @return True if building is restricted to sectors with building rights.
	 */
	static bool AreSectorsEnabled();

	/**
	 * Check whether a sector exists.
	 * @param sector_id The sector to check.
	 * @return True if the sector exists.
	 */
	static bool IsValidSector(SectorID sector_id);

	/**
	 * Get the sector of a tile.
	 * @param tile The tile.
	 * @pre ScriptMap::IsValidTile(tile).
	 * @return The sector of the tile, or an invalid sector if the map has no sectors.
	 */
	static SectorID GetSector(TileIndex tile);

	/**
	 * Get the name of a sector.
	 * @param sector_id The sector.
	 * @pre IsValidSector(sector_id).
	 * @return The name of the sector.
	 */
	static std::optional<std::string> GetName(SectorID sector_id);

	/**
	 * Get the number of tiles of a sector.
	 * @param sector_id The sector.
	 * @pre IsValidSector(sector_id).
	 * @return The number of tiles, -1 if the sector is invalid.
	 */
	static SQInteger GetTileCount(SectorID sector_id);

	/**
	 * Get a tile near the centre of a sector, which is part of the sector.
	 * @param sector_id The sector.
	 * @pre IsValidSector(sector_id).
	 * @return The tile.
	 */
	static TileIndex GetCentre(SectorID sector_id);

	/**
	 * Get the price of the building rights of a sector.
	 * In company mode this takes the free first sector of the company into account.
	 * @param sector_id The sector.
	 * @pre IsValidSector(sector_id).
	 * @return The price, -1 if the sector is invalid.
	 */
	static Money GetPrice(SectorID sector_id);

	/**
	 * Check whether a sector can be the free first sector of a company.
	 * @param sector_id The sector.
	 * @pre IsValidSector(sector_id).
	 * @return True if the first building rights of a company in this sector are free.
	 */
	static bool CanBeFreeFirstSector(SectorID sector_id);

	/**
	 * Check whether a company owns building rights in a sector.
	 * @param company The company.
	 * @param sector_id The sector.
	 * @pre IsValidSector(sector_id).
	 * @pre ScriptCompany::ResolveCompanyID(company) != ScriptCompany::COMPANY_INVALID.
	 * @return True if the company owns building rights in the sector.
	 */
	static bool HasBuildingRights(ScriptCompany::CompanyID company, SectorID sector_id);

	/**
	 * Check whether a company may build on a tile.
	 * @param company The company.
	 * @param tile The tile.
	 * @pre ScriptMap::IsValidTile(tile).
	 * @pre ScriptCompany::ResolveCompanyID(company) != ScriptCompany::COMPANY_INVALID.
	 * @return True if sectors are disabled, the tile has no sector, or the company owns building rights in its sector.
	 */
	static bool CanBuildOnTile(ScriptCompany::CompanyID company, TileIndex tile);

	/**
	 * Buy building rights in a sector for your company.
	 * @param sector_id The sector.
	 * @pre IsValidSector(sector_id).
	 * @pre AreSectorsEnabled().
	 * @game @pre ScriptCompanyMode::IsValid().
	 * @exception ScriptSector::ERR_SECTOR_ALREADY_HAVE_RIGHTS
	 * @exception ScriptError::ERR_NOT_ENOUGH_CASH
	 * @return True if the building rights were bought.
	 */
	static bool BuyBuildingRights(SectorID sector_id);
};

#endif /* SCRIPT_SECTOR_HPP */
