/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file overseas_sl.cpp Code handling saving and loading of overseas stations. */

#include "../stdafx.h"
#include "../overseas.h"
#include "../station_base.h"

#include "saveload.h"

#include "../safeguards.h"

/** Description of the overseas data of a station within the savegame. */
static const NamedSaveLoad _overseas_desc[] = {
	NSL("country",        SLE_VAR(OverseasStation, country,        SLE_UINT8)),
	NSL("cargo",          SLE_VAR(OverseasStation, cargo,          SLE_UINT8)),
	NSL("accepted_cargo", SLE_VAR(OverseasStation, accepted_cargo, SLE_UINT8)),
	NSL("growth",         SLE_VAR(OverseasStation, growth,         SLE_UINT32)),
	NSL("fract",          SLE_ARR(OverseasStation, fract,          SLE_UINT32, NUM_OVERSEAS_PRODUCTS)),
};

/** Save the overseas data of all overseas stations, indexed by station. */
static void Save_OVRS()
{
	SaveLoadTableData slt = SlTableHeader(_overseas_desc);

	for (Station *st : Station::Iterate()) {
		if (st->overseas == nullptr) continue;
		SlSetArrayIndex(st->index);
		SlObjectSaveFiltered(st->overseas.get(), slt);
	}
}

/** Load the overseas data of the overseas stations; the stations are loaded before. */
static void Load_OVRS()
{
	SaveLoadTableData slt = SlTableHeader(_overseas_desc);

	int index;
	while ((index = SlIterateArray()) != -1) {
		Station *st = Station::GetIfValid(StationID(index));
		if (st == nullptr) SlErrorCorruptFmt("Overseas data for invalid station: {}", index);
		st->overseas = std::make_unique<OverseasStation>();
		SlObjectLoadFiltered(st->overseas.get(), slt);
	}
}

/** Chunk handlers related to overseas stations. */
static const ChunkHandler overseas_chunk_handlers[] = {
	{ 'OVRS', Save_OVRS, Load_OVRS, nullptr, nullptr, ChunkType::Table },
};

extern const ChunkHandlerTable _overseas_chunk_handlers(overseas_chunk_handlers);
