/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file used_vehicle.cpp Test the age limit of used vehicles generated at the start of the game. */

#include "../stdafx.h"

#include "../3rdparty/catch2/catch.hpp"

#include "../used_vehicle.h"

#include "../safeguards.h"

static CalTime::DateDelta Days(int32_t days) { return CalTime::DateDelta{days}; }

TEST_CASE("GetUsedVehicleMaxAgeYears - limited by the time since introduction")
{
	/* 15 year lifespan, as Engine::GetLifeLengthInDays counts it. */
	const CalTime::DateDelta life = Days(15 * DAYS_IN_LEAP_YEAR);
	CHECK(GetUsedVehicleMaxAgeYears(life, Days(0)) == 0);
	CHECK(GetUsedVehicleMaxAgeYears(life, Days(DAYS_IN_YEAR - 1)) == 0);
	CHECK(GetUsedVehicleMaxAgeYears(life, Days(DAYS_IN_YEAR)) == 1);
	CHECK(GetUsedVehicleMaxAgeYears(life, Days(3 * DAYS_IN_YEAR + 200)) == 3);
}

TEST_CASE("GetUsedVehicleMaxAgeYears - limited by the lifespan")
{
	CHECK(GetUsedVehicleMaxAgeYears(Days(15 * DAYS_IN_LEAP_YEAR), Days(40 * DAYS_IN_YEAR)) == 15);
	CHECK(GetUsedVehicleMaxAgeYears(Days(1 * DAYS_IN_LEAP_YEAR), Days(40 * DAYS_IN_YEAR)) == 1);
	CHECK(GetUsedVehicleMaxAgeYears(Days(0), Days(40 * DAYS_IN_YEAR)) == 0);
}

TEST_CASE("GetUsedVehicleMaxAgeYears - engine introduced in the future")
{
	/* Engines available before their introduction date (e.g. via cheats or NewGRF) must not give a negative age. */
	CHECK(GetUsedVehicleMaxAgeYears(Days(15 * DAYS_IN_LEAP_YEAR), Days(-500)) == 0);
}
