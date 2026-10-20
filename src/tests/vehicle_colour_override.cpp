/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file vehicle_colour_override.cpp Test the livery choice of own/opponent vehicle colours. */

#include "../stdafx.h"

#include "../3rdparty/catch2/catch.hpp"

#include "../vehicle_colour_override.h"

#include "../safeguards.h"

static const CompanyID OWN{0};
static const CompanyID OTHER{3};
static const VehicleColourOverrideColours DEFAULTS{Colours::Yellow, Colours::Orange, Colours::Red, Colours::Brown};

TEST_CASE("VehicleColourOverride - switched off keeps the normal livery")
{
	CHECK_FALSE(GetVehicleColourOverrideLivery(false, OWN, true, OWN, DEFAULTS).has_value());
	CHECK_FALSE(GetVehicleColourOverrideLivery(false, OWN, true, OTHER, DEFAULTS).has_value());
}

TEST_CASE("VehicleColourOverride - own vehicles use the own colours")
{
	const std::optional<Livery> livery = GetVehicleColourOverrideLivery(true, OWN, true, OWN, DEFAULTS);
	REQUIRE(livery.has_value());
	CHECK(livery->colour1 == Colours::Yellow);
	CHECK(livery->colour2 == Colours::Orange);
	CHECK(livery->custom1 == CUSTOM_COLOUR_NONE);
	CHECK(livery->custom2 == CUSTOM_COLOUR_NONE);
	CHECK(livery->in_use.All({Livery::Flag::Primary, Livery::Flag::Secondary}));
}

TEST_CASE("VehicleColourOverride - other companies' vehicles use the opponent colours")
{
	const std::optional<Livery> livery = GetVehicleColourOverrideLivery(true, OWN, true, OTHER, DEFAULTS);
	REQUIRE(livery.has_value());
	CHECK(livery->colour1 == Colours::Red);
	CHECK(livery->colour2 == Colours::Brown);
}

TEST_CASE("VehicleColourOverride - configured colours are used")
{
	const VehicleColourOverrideColours colours{Colours::White, Colours::Grey, Colours::DarkBlue, Colours::Pink};
	const std::optional<Livery> own = GetVehicleColourOverrideLivery(true, OTHER, true, OTHER, colours);
	const std::optional<Livery> other = GetVehicleColourOverrideLivery(true, OTHER, true, OWN, colours);
	REQUIRE(own.has_value());
	REQUIRE(other.has_value());
	CHECK(own->colour1 == Colours::White);
	CHECK(own->colour2 == Colours::Grey);
	CHECK(other->colour1 == Colours::DarkBlue);
	CHECK(other->colour2 == Colours::Pink);
	/* The livery palette offset combines both colours. */
	CHECK(own->GetRecolourOffset(true) == to_underlying(Colours::White) + to_underlying(Colours::Grey) * 16);
	CHECK(own->GetRecolourOffset(false) == to_underlying(Colours::White));
}

TEST_CASE("VehicleColourOverride - spectators see the normal liveries")
{
	CHECK_FALSE(GetVehicleColourOverrideLivery(true, COMPANY_SPECTATOR, false, OWN, DEFAULTS).has_value());
	CHECK_FALSE(GetVehicleColourOverrideLivery(true, OWNER_NONE, false, OTHER, DEFAULTS).has_value());
}
