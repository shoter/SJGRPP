/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file vehicle_colour_override.h Own/opponent vehicle colours: draw all own vehicles in one livery and all other companies' vehicles in another. */

#ifndef VEHICLE_COLOUR_OVERRIDE_H
#define VEHICLE_COLOUR_OVERRIDE_H

#include "livery.h"

#include <optional>

/** Colours used when own/opponent vehicle colours are shown. */
struct VehicleColourOverrideColours {
	Colours own_primary;     ///< Primary colour of the local company's vehicles.
	Colours own_secondary;   ///< Secondary colour of the local company's vehicles.
	Colours other_primary;   ///< Primary colour of other companies' vehicles.
	Colours other_secondary; ///< Secondary colour of other companies' vehicles.
};

/**
 * Get the livery a vehicle is drawn with when own/opponent vehicle colours are shown.
 * @param enabled Whether own/opponent vehicle colours are switched on.
 * @param local_company The company of this client; a spectator or #OWNER_NONE has no own vehicles, so nothing is overridden.
 * @param local_company_valid Whether \p local_company is a real company.
 * @param owner Owner of the vehicle.
 * @param colours The configured colours.
 * @return The livery to draw with, or std::nullopt to keep the normal livery.
 */
inline std::optional<Livery> GetVehicleColourOverrideLivery(bool enabled, CompanyID local_company, bool local_company_valid, CompanyID owner, const VehicleColourOverrideColours &colours)
{
	if (!enabled || !local_company_valid) return std::nullopt;

	const bool own = owner == local_company;
	Livery livery{};
	livery.in_use = {Livery::Flag::Primary, Livery::Flag::Secondary};
	livery.colour1 = own ? colours.own_primary : colours.other_primary;
	livery.colour2 = own ? colours.own_secondary : colours.other_secondary;
	return livery;
}

extern bool _vehicle_colour_override;

void ToggleVehicleColourOverride();

#endif /* VEHICLE_COLOUR_OVERRIDE_H */
