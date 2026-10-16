/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file script_sectorlist.hpp List all the sectors. */

#ifndef SCRIPT_SECTORLIST_HPP
#define SCRIPT_SECTORLIST_HPP

#include "script_list.hpp"

/**
 * Create a list of all sectors.
 * @api ai game
 * @ingroup ScriptList
 */
class ScriptSectorList : public ScriptList {
public:
#ifdef DOXYGEN_API
	ScriptSectorList();

	/**
	 * Apply a filter when building the list.
	 * @param filter_function The function which will be doing the filtering.
	 * @param ... The params to give to the filters (minus the first param,
	 *  which is always the index-value).
	 * @note You can write your own filters and use them. Just remember that
	 *  the first parameter should be the index-value, and it should return
	 *  a bool.
	 * @note Example:
	 * @code
	 *  function Unowned(sector_id)
	 *  {
	 *    return !ScriptSector.HasBuildingRights(ScriptCompany.COMPANY_SELF, sector_id);
	 *  }
	 *  local sectors = ScriptSectorList(Unowned);
	 * @endcode
	 */
	ScriptSectorList(void *filter_function, ...);
#else
	/**
	 * The constructor wrapper from Squirrel.
	 */
	ScriptSectorList(HSQUIRRELVM);
#endif /* DOXYGEN_API */
};

#endif /* SCRIPT_SECTORLIST_HPP */
