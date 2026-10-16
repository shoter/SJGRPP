/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file script_sector.cpp Implementation of ScriptSector. */

#include "../../stdafx.h"
#include "script_sector.hpp"
#include "script_map.hpp"
#include "../../company_base.h"
#include "../../sector_base.h"
#include "../../sector_cmd.h"
#include "../../sector_func.h"
#include "../../string_func.h"

#include "../../safeguards.h"

/* static */ bool ScriptSector::AreSectorsEnabled()
{
	return ::AreSectorsEnabled();
}

/* static */ bool ScriptSector::IsValidSector(SectorID sector_id)
{
	return ::Sector::IsValidID(sector_id);
}

/* static */ SectorID ScriptSector::GetSector(TileIndex tile)
{
	if (!ScriptMap::IsValidTile(tile)) return SectorID::Invalid();
	return ::GetTileSector(tile);
}

/* static */ std::optional<std::string> ScriptSector::GetName(SectorID sector_id)
{
	if (!IsValidSector(sector_id)) return std::nullopt;
	return ::StrMakeValid(::GetSectorName(sector_id), {});
}

/* static */ SQInteger ScriptSector::GetTileCount(SectorID sector_id)
{
	if (!IsValidSector(sector_id)) return -1;
	return ::Sector::Get(sector_id)->tile_count;
}

/* static */ TileIndex ScriptSector::GetCentre(SectorID sector_id)
{
	if (!IsValidSector(sector_id)) return INVALID_TILE;
	return ::Sector::Get(sector_id)->centre;
}

/* static */ Money ScriptSector::GetPrice(SectorID sector_id)
{
	if (!IsValidSector(sector_id)) return -1;
	const ::Sector *s = ::Sector::Get(sector_id);
	::CompanyID company = ScriptObject::GetCompany();
	if (::Company::IsValidID(company)) return s->GetPriceFor(company);
	return s->GetPrice();
}

/* static */ bool ScriptSector::CanBeFreeFirstSector(SectorID sector_id)
{
	if (!IsValidSector(sector_id)) return false;
	return !::Sector::Get(sector_id)->no_first_pick;
}

/* static */ bool ScriptSector::HasBuildingRights(ScriptCompany::CompanyID company, SectorID sector_id)
{
	if (!IsValidSector(sector_id)) return false;
	company = ScriptCompany::ResolveCompanyID(company);
	if (company == ScriptCompany::COMPANY_INVALID) return false;
	return ::Sector::Get(sector_id)->HasRights(ScriptCompany::FromScriptCompanyID(company));
}

/* static */ bool ScriptSector::CanBuildOnTile(ScriptCompany::CompanyID company, TileIndex tile)
{
	if (!ScriptMap::IsValidTile(tile)) return false;
	company = ScriptCompany::ResolveCompanyID(company);
	if (company == ScriptCompany::COMPANY_INVALID) return false;
	if (!::AreSectorsEnabled()) return true;
	const ::Sector *s = ::Sector::GetIfValid(::GetTileSector(tile));
	return s == nullptr || s->HasRights(ScriptCompany::FromScriptCompanyID(company));
}

/* static */ bool ScriptSector::BuyBuildingRights(SectorID sector_id)
{
	EnforceCompanyModeValid(false);
	EnforcePrecondition(false, IsValidSector(sector_id));
	EnforcePrecondition(false, ::AreSectorsEnabled());

	return ScriptObject::Command<Commands::BuySectorRights>::Do(sector_id);
}
