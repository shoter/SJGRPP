/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file sector_sl.cpp Code handling saving and loading of sectors. */

#include "../stdafx.h"
#include "../sector_base.h"
#include "../sector_func.h"
#include "../map_func.h"
#include "../core/format.hpp"

#include "saveload.h"

#include "../safeguards.h"

/** Description of a sector within the savegame. */
static const NamedSaveLoad _sector_desc[] = {
	NSL("name",          SLE_SSTR(Sector, name,          SLE_STR | SLF_ALLOW_CONTROL)),
	NSL("custom_price",  SLE_VAR(Sector, custom_price,   SLE_INT64)),
	NSL("no_first_pick", SLE_VAR(Sector, no_first_pick,  SLE_BOOL)),
	NSL("locked",        SLE_VAR(Sector, locked,         SLE_BOOL)),
	NSL("rights",        SLE_VAR(Sector, rights,         SLE_UINT32)),
	NSL("town",          SLE_VAR(Sector, town,           SLE_UINT16)),
};

/** Save all sectors. */
static void Save_SECT()
{
	SaveLoadTableData slt = SlTableHeader(_sector_desc);

	for (Sector *s : Sector::Iterate()) {
		SlSetArrayIndex(s->index);
		SlObjectSaveFiltered(s, slt);
	}
}

/** Load all sectors. */
static void Load_SECT()
{
	SaveLoadTableData slt = SlTableHeader(_sector_desc);

	int index;
	while ((index = SlIterateArray()) != -1) {
		Sector *s = Sector::CreateAtIndex(SectorID(index));
		SlObjectLoadFiltered(s, slt);
	}
}

/** Save the sector of each tile, nothing if the map has no sectors. */
static void Save_SECM()
{
	if (!MapHasSectors()) {
		SlSetLength(0);
		return;
	}
	SlSetLength(_sector_map.size());
	SlCopyBytesWrite(_sector_map.data(), _sector_map.size());
}

/** Load the sector of each tile. */
static void Load_SECM()
{
	size_t length = SlGetFieldLength();
	if (length == 0) {
		InitializeSectorMap();
		return;
	}
	if (length != Map::Size()) SlErrorCorruptFmt("Invalid sector map size: {}, expected {}", length, Map::Size());
	_sector_map.resize(length);
	SlCopyBytesRead(_sector_map.data(), length);
}

/** Chunk handlers related to sectors. */
static const ChunkHandler sector_chunk_handlers[] = {
	{ 'SECT', Save_SECT, Load_SECT, nullptr, nullptr, ChunkType::Table },
	{ 'SECM', Save_SECM, Load_SECM, nullptr, nullptr, ChunkType::Riff },
};

extern const ChunkHandlerTable _sector_chunk_handlers(sector_chunk_handlers);
