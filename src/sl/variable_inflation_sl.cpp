/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file variable_inflation_sl.cpp Code handling saving and loading of the variable inflation rates and their history. */

#include "../stdafx.h"
#include "../variable_inflation.h"

#include "saveload.h"

#include "../safeguards.h"

/** Description of the current inflation rates. */
static const NamedSaveLoad _variable_inflation_desc[] = {
	NSL("cost_rate",   SLE_VAR(VariableInflation, cost_rate,   SLE_UINT16)),
	NSL("income_rate", SLE_VAR(VariableInflation, income_rate, SLE_UINT16)),
};

/** Description of one year of inflation history. */
static const NamedSaveLoad _inflation_history_desc[] = {
	NSL("year",              SLE_VAR(InflationHistoryEntry, year,              SLE_INT32)),
	NSL("cost_rate",         SLE_VAR(InflationHistoryEntry, cost_rate,         SLE_UINT16)),
	NSL("income_rate",       SLE_VAR(InflationHistoryEntry, income_rate,       SLE_UINT16)),
	NSL("inflation_prices",  SLE_VAR(InflationHistoryEntry, inflation_prices,  SLE_UINT64)),
	NSL("inflation_payment", SLE_VAR(InflationHistoryEntry, inflation_payment, SLE_UINT64)),
};

static void Save_INFL()
{
	SlSaveTableObjectChunk(_variable_inflation_desc, &_variable_inflation);
}

static void Load_INFL()
{
	SlLoadTableObjectChunk(_variable_inflation_desc, &_variable_inflation);
}

/** Save the inflation history, oldest year first. */
static void Save_INFH()
{
	SaveLoadTableData slt = SlTableHeader(_inflation_history_desc);

	for (uint i = 0; i < _variable_inflation.history_count; i++) {
		SlSetArrayIndex(i);
		SlObjectSaveFiltered(&_variable_inflation.history[i], slt);
	}
}

static void Load_INFH()
{
	SaveLoadTableData slt = SlTableHeader(_inflation_history_desc);

	_variable_inflation.history_count = 0;
	while (SlIterateArray() != -1) {
		InflationHistoryEntry entry;
		SlObjectLoadFiltered(&entry, slt);
		_variable_inflation.AddHistory(entry);
	}
}

/** Chunk handlers related to the variable inflation. */
static const ChunkHandler variable_inflation_chunk_handlers[] = {
	{ 'INFL', Save_INFL, Load_INFL, nullptr, nullptr, ChunkType::Table },
	{ 'INFH', Save_INFH, Load_INFH, nullptr, nullptr, ChunkType::Table },
};

extern const ChunkHandlerTable _variable_inflation_chunk_handlers(variable_inflation_chunk_handlers);
