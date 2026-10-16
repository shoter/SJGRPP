/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file sector_cmd.cpp Commands related to sectors. */

#include "stdafx.h"
#include "sector_base.h"
#include "sector_cmd.h"
#include "sector_func.h"
#include "command_func.h"
#include "company_base.h"
#include "company_func.h"
#include "map_func.h"
#include "openttd.h"
#include "string_func.h"
#include "tilearea_type.h"
#include "window_func.h"

#include "table/strings.h"

#include "safeguards.h"

/**
 * Buy building rights in a sector for the current company.
 * The first sector of a company is free, unless that sector does not allow being picked first.
 * @param flags Type of operation.
 * @param sector The sector.
 * @return The cost of this operation or an error.
 */
CommandCost CmdBuySectorRights(DoCommandFlags flags, SectorID sector)
{
	if (_game_mode != GameMode::Normal || !AreSectorsEnabled()) return CMD_ERROR;
	if (!Company::IsValidID(_current_company)) return CMD_ERROR;

	Sector *s = Sector::GetIfValid(sector);
	if (s == nullptr) return CMD_ERROR;
	if (s->HasRights(_current_company)) return CommandCost(STR_ERROR_SECTOR_ALREADY_HAVE_RIGHTS);

	CommandCost cost(ExpensesType::Construction, s->GetPriceFor(_current_company));

	if (flags.Test(DoCommandFlag::Execute)) {
		s->rights.Set(_current_company);
		InvalidateWindowData(WindowClass::Sectors, 0);
		MarkSectorOverlayDirty();
	}

	return cost;
}

/** Remove sectors without tiles, after their tiles were given to other sectors. */
static void DeleteEmptySectors()
{
	for (Sector *s : Sector::Iterate()) {
		if (s->tile_count == 0) delete s;
	}
}

/**
 * Assign an area of the map to a sector (scenario editor only).
 * @param flags Type of operation.
 * @param tile End tile of the area.
 * @param start_tile Start tile of the area.
 * @param sector The sector the tiles are assigned to.
 * @param diagonal Whether to use the diagonal area.
 * @return The cost of this operation or an error.
 */
CommandCost CmdSectorPaint(DoCommandFlags flags, TileIndex tile, TileIndex start_tile, SectorID sector, bool diagonal)
{
	if (_game_mode != GameMode::Editor) return CMD_ERROR;
	if (start_tile >= Map::Size()) return CMD_ERROR;
	if (!Sector::IsValidID(sector) || !MapHasSectors()) return CMD_ERROR;

	if (flags.Test(DoCommandFlag::Execute)) {
		OrthogonalOrDiagonalTileIterator iter(tile, start_tile, diagonal);
		for (; *iter != INVALID_TILE; ++iter) {
			SetTileSector(*iter, sector);
		}
		UpdateSectorCaches();
		DeleteEmptySectors();
		MarkSectorOverlayDirty();
	}

	return CommandCost();
}

/**
 * Create a new sector consisting of a single tile (scenario editor only).
 * @param flags Type of operation.
 * @param tile The first tile of the sector.
 * @return The cost of this operation, with the ID of the new sector, or an error.
 */
CommandCost CmdSectorCreate(DoCommandFlags flags, TileIndex tile)
{
	if (_game_mode != GameMode::Editor) return CMD_ERROR;
	if (!Sector::CanAllocateItem()) return CommandCost(STR_ERROR_SECTOR_TOO_MANY);

	if (flags.Test(DoCommandFlag::Execute)) {
		Sector *s = Sector::Create();
		SetTileSector(tile, s->index);
		UpdateSectorCaches();
		DeleteEmptySectors();
		AssignSectorTowns(true);
		MarkSectorOverlayDirty();

		CommandCost cost;
		cost.SetResultData(s->index);
		return cost;
	}

	return CommandCost();
}

/**
 * Rename a sector (scenario editor only).
 * @param flags Type of operation.
 * @param sector The sector.
 * @param text The new name, empty for the automatic name.
 * @return The cost of this operation or an error.
 */
CommandCost CmdSectorRename(DoCommandFlags flags, SectorID sector, const std::string &text)
{
	if (_game_mode != GameMode::Editor) return CMD_ERROR;
	Sector *s = Sector::GetIfValid(sector);
	if (s == nullptr) return CMD_ERROR;
	if (Utf8StringLength(text) >= MAX_LENGTH_SECTOR_NAME_CHARS) return CMD_ERROR;

	if (flags.Test(DoCommandFlag::Execute)) {
		s->name = text;
		InvalidateWindowData(WindowClass::Sectors, 0);
	}

	return CommandCost();
}

/**
 * Change the price and the first pick flag of a sector (scenario editor only).
 * @param flags Type of operation.
 * @param sector The sector.
 * @param price The custom base price, 0 for a price based on the number of tiles.
 * @param no_first_pick Whether the sector cannot be the free first sector of a company.
 * @return The cost of this operation or an error.
 */
CommandCost CmdSectorSetProperties(DoCommandFlags flags, SectorID sector, Money price, bool no_first_pick)
{
	if (_game_mode != GameMode::Editor) return CMD_ERROR;
	Sector *s = Sector::GetIfValid(sector);
	if (s == nullptr) return CMD_ERROR;
	if (price < 0) return CMD_ERROR;

	if (flags.Test(DoCommandFlag::Execute)) {
		s->custom_price = price;
		s->no_first_pick = no_first_pick;
		InvalidateWindowData(WindowClass::Sectors, 0);
		MarkSectorOverlayDirty();
	}

	return CommandCost();
}

/**
 * Divide the map randomly into sectors, replacing all existing sectors (scenario editor only).
 * @param flags Type of operation.
 * @param average_size The average number of tiles of a sector.
 * @return The cost of this operation or an error.
 */
CommandCost CmdSectorGenerate(DoCommandFlags flags, uint32_t average_size)
{
	if (_game_mode != GameMode::Editor) return CMD_ERROR;
	if (average_size < 64) return CMD_ERROR;

	if (flags.Test(DoCommandFlag::Execute)) {
		GenerateSectors(average_size);
	}

	return CommandCost();
}

/**
 * Merge a sector into another one (scenario editor only).
 * @param flags Type of operation.
 * @param sector The sector which is removed.
 * @param into The sector which receives its tiles.
 * @return The cost of this operation or an error.
 */
CommandCost CmdSectorMerge(DoCommandFlags flags, SectorID sector, SectorID into)
{
	if (_game_mode != GameMode::Editor) return CMD_ERROR;
	if (!Sector::IsValidID(sector) || !Sector::IsValidID(into) || sector == into) return CMD_ERROR;

	if (flags.Test(DoCommandFlag::Execute)) {
		for (uint8_t &s : _sector_map) {
			if (s == sector.base()) s = into.base();
		}
		delete Sector::Get(sector);
		UpdateSectorCaches();
		MarkSectorOverlayDirty();
	}

	return CommandCost();
}
