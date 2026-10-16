/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file sector_func.h Functions related to sectors. */

#ifndef SECTOR_FUNC_H
#define SECTOR_FUNC_H

#include "sector_type.h"
#include "command_type.h"
#include "company_type.h"
#include "tile_type.h"
#include "map_type.h"
#include "tilearea_type.h"
#include <vector>

extern std::vector<uint8_t> _sector_map;
extern bool _show_sector_overlay;
extern SectorID _highlighted_sector;
extern bool _sector_checks_suspended;

/**
 * Get the sector of a tile.
 * @param tile The tile.
 * @return The sector, or SectorID::Invalid() when the map has no sectors.
 */
inline SectorID GetTileSector(TileIndex tile)
{
	if (_sector_map.empty()) return SectorID::Invalid();
	return SectorID(_sector_map[tile.base()]);
}

bool AreSectorsEnabled();
bool MapHasSectors();
void InitializeSectorMap();
void EnsureSectorMap();
void SetTileSector(TileIndex tile, SectorID sector);

void GenerateSectors(uint32_t average_size);
void GenerateSectorsIfMissing();
void UpdateSectorCaches();
void AssignSectorTowns(bool only_missing);
void ClearSectorRightsOfCompany(CompanyID company);
void AfterLoadSectors();

std::string GetSectorName(SectorID sector);
bool CompanyHasAnySectorRights(CompanyID company);

CommandCost CheckSectorBuildingRights(TileIndex tile);
CommandCost CheckSectorBuildingRights(const TileArea &area);
CommandCost CheckSectorBuildingRightsLine(TileIndex begin, TileIndex end);

void MarkSectorOverlayDirty();
void ToggleSectorOverlay();
void ShowSectorWindow();
void DrawTileSectorOverlay(const struct TileInfo *ti);

#endif /* SECTOR_FUNC_H */
