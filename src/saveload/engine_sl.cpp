/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file engine_sl.cpp Code handling saving and loading of engines. */

#include "../stdafx.h"

#include "saveload.h"
#include "compat/engine_sl_compat.h"

#include "../engine_base.h"
#include "../engine_override.h"
#include "../string_func.h"
#include <vector>

#include "../safeguards.h"

Engine *GetTempDataEngine(EngineID index, VehicleType type, uint16_t local_id);

namespace upstream_sl {

/* The company masks are 32 bit, the low 16 bits are stored in the original fields and the high 16 bits are stored separately. */
static uint16_t _engine_preview_asked_lo, _engine_preview_asked_hi;
static uint16_t _engine_company_avail_lo, _engine_company_avail_hi;
static uint16_t _engine_company_hidden_lo, _engine_company_hidden_hi;

static const SaveLoad _engine_desc[] = {
	 SLE_CONDVAR(Engine, intro_date, VarFileType::U16 | VarMemType::I32, SaveLoadVersion::MinVersion, SaveLoadVersion::BigDates),
	 SLE_CONDVAR(Engine, intro_date, VarTypes::I32, SaveLoadVersion::BigDates, SaveLoadVersion::MaxVersion),
	 SLE_CONDVAR(Engine, age, VarFileType::U16 | VarMemType::I32, SaveLoadVersion::MinVersion, SaveLoadVersion::BigDates),
	 SLE_CONDVAR(Engine, age, VarTypes::I32, SaveLoadVersion::BigDates, SaveLoadVersion::MaxVersion),
	     SLE_VAR(Engine, reliability,         VarTypes::U16),
	     SLE_VAR(Engine, reliability_spd_dec, VarTypes::U16),
	     SLE_VAR(Engine, reliability_start,   VarTypes::U16),
	     SLE_VAR(Engine, reliability_max,     VarTypes::U16),
	     SLE_VAR(Engine, reliability_final,   VarTypes::U16),
	     SLE_VAR(Engine, duration_phase_1,    VarTypes::U16),
	     SLE_VAR(Engine, duration_phase_2,    VarTypes::U16),
	     SLE_VAR(Engine, duration_phase_3,    VarTypes::U16),
	     SLE_VAR(Engine, flags,               VarTypes::U8),
	SLEG_CONDVAR("preview_asked", _engine_preview_asked_lo, VarTypes::U16, SaveLoadVersion::RobustEnginePreview, SaveLoadVersion::MaxVersion),
	 SLE_CONDVAR(Engine, preview_company, VarTypes::U8, SaveLoadVersion::RobustEnginePreview, SaveLoadVersion::MaxVersion),
	     SLE_VAR(Engine, preview_wait,        VarTypes::U8),
	SLEG_CONDVAR("company_avail", _engine_company_avail_lo, VarFileType::U8 | VarMemType::U16, SaveLoadVersion::MinVersion, SaveLoadVersion::MoreCompanies),
	SLEG_CONDVAR("company_avail", _engine_company_avail_lo, VarTypes::U16, SaveLoadVersion::MoreCompanies, SaveLoadVersion::MaxVersion),
	SLEG_CONDVAR("company_hidden", _engine_company_hidden_lo, VarTypes::U16, SaveLoadVersion::HideEnginesForCompany, SaveLoadVersion::MaxVersion),
	    SLEG_VAR("preview_asked_hi", _engine_preview_asked_hi, VarTypes::U16),
	    SLEG_VAR("company_avail_hi", _engine_company_avail_hi, VarTypes::U16),
	    SLEG_VAR("company_hidden_hi", _engine_company_hidden_hi, VarTypes::U16),
	 SLE_CONDSTR(Engine, name, VarTypes::STR, 0, SaveLoadVersion::ReplaceCustomNameArray, SaveLoadVersion::MaxVersion),
};

struct ENGNChunkHandler : ChunkHandler {
	ENGNChunkHandler() : ChunkHandler("ENGN", ChunkType::Table) {}

	void Save() const override
	{
		SlTableHeader(_engine_desc);

		for (Engine *e : Engine::Iterate()) {
			SlSetArrayIndex(e->index);
			_engine_preview_asked_lo = GB(e->preview_asked.base(), 0, 16);
			_engine_preview_asked_hi = GB(e->preview_asked.base(), 16, 16);
			_engine_company_avail_lo = GB(e->company_avail.base(), 0, 16);
			_engine_company_avail_hi = GB(e->company_avail.base(), 16, 16);
			_engine_company_hidden_lo = GB(e->company_hidden.base(), 0, 16);
			_engine_company_hidden_hi = GB(e->company_hidden.base(), 16, 16);
			SlObject(e, _engine_desc);
		}
	}

	void Load() const override
	{
		const std::vector<SaveLoad> slt = SlCompatTableHeader(_engine_desc, _engine_sl_compat);

		/* As engine data is loaded before engines are initialized we need to load
		 * this information into a temporary array. This is then copied into the
		 * engine pool after processing NewGRFs by CopyTempEngineData(). */
		int index;
		while ((index = SlIterateArray()) != -1) {
			Engine *e = GetTempDataEngine(static_cast<EngineID>(index), VehicleType::Invalid, 0);
			_engine_preview_asked_lo = _engine_preview_asked_hi = 0;
			_engine_company_avail_lo = _engine_company_avail_hi = 0;
			_engine_company_hidden_lo = _engine_company_hidden_hi = 0;
			SlObject(e, slt);
			e->preview_asked.edit_base() = _engine_preview_asked_lo | (static_cast<uint32_t>(_engine_preview_asked_hi) << 16);
			e->company_avail.edit_base() = _engine_company_avail_lo | (static_cast<uint32_t>(_engine_company_avail_hi) << 16);
			e->company_hidden.edit_base() = _engine_company_hidden_lo | (static_cast<uint32_t>(_engine_company_hidden_hi) << 16);

			if (IsSavegameVersionBefore(SaveLoadVersion::RobustEnginePreview)) {
				/* preview_company_rank was replaced with preview_company and preview_asked.
				 * Just cancel any previews. */
				e->flags.Reset(EngineFlag{2}); // ENGINE_OFFER_WINDOW_OPEN
				e->preview_company = CompanyID::Invalid();
				e->preview_asked.Set();
			}
		}
	}
};

/** Save and load the mapping between the engine id in the pool, and the grf file it came from. */
static const SaveLoad _engine_id_mapping_desc[] = {
	SLE_VAR(EngineIDMapping, grfid, VarTypes::LABEL),
	SLE_VAR(EngineIDMapping, internal_id,   VarTypes::U16),
	SLE_VAR(EngineIDMapping, type,          VarTypes::U8),
	SLE_VAR(EngineIDMapping, substitute_id, VarTypes::U8),
};

struct EIDSChunkHandler : ChunkHandler {
	EIDSChunkHandler() : ChunkHandler("EIDS", ChunkType::Table) {}

	void Save() const override
	{
		SlTableHeader(_engine_id_mapping_desc);

		uint index = 0;
		for (EngineIDMapping &eid : _engine_mngr.mappings) {
			SlSetArrayIndex(index);
			SlObject(&eid, _engine_id_mapping_desc);
			index++;
		}
	}

	void Load() const override
	{
		const std::vector<SaveLoad> slt = SlCompatTableHeader(_engine_id_mapping_desc, _engine_id_mapping_sl_compat);

		_engine_mngr.mappings.clear();

		while (SlIterateArray() != -1) {
			EngineIDMapping *eid = &_engine_mngr.mappings.emplace_back();
			SlObject(eid, slt);
		}

		_engine_mngr.ReIndex();
	}
};

static const EIDSChunkHandler EIDS;
static const ENGNChunkHandler ENGN;
static const ChunkHandlerRef engine_chunk_handlers[] = {
	EIDS,
	ENGN,
};

extern const ChunkHandlerTable _engine_chunk_handlers(engine_chunk_handlers);

}
