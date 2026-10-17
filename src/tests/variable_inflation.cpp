/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file variable_inflation.cpp Test the yearly change of inflation rates and their history. */

#include "../stdafx.h"

#include "../3rdparty/catch2/catch.hpp"

#include "../core/bitmath_func.hpp"
#include "../variable_inflation.h"

#include "../safeguards.h"

/** Random bits for ChangeInflationRate: change roll and direction roll, each 0..65535. */
static uint32_t Rnd(uint16_t change_roll, uint16_t direction_roll)
{
	return change_roll | (static_cast<uint32_t>(direction_roll) << 16);
}

TEST_CASE("ChangeInflationRate - chance of a change")
{
	/* Change roll 0 is below any non-zero chance. */
	CHECK(ChangeInflationRate(50, 30, 80, 80, Rnd(0, 0)) != 50);
	/* Chance 0: never changes. */
	CHECK(ChangeInflationRate(50, 30, 80, 0, Rnd(0, 0)) == 50);
	/* 80%: rolls below 80% of 65536 change, rolls at or above do not. */
	CHECK(ChangeInflationRate(50, 30, 80, 80, Rnd(52428, 0)) != 50);
	CHECK(ChangeInflationRate(50, 30, 80, 80, Rnd(52429, 0)) == 50);
	CHECK(ChangeInflationRate(50, 30, 80, 80, Rnd(65535, 0)) == 50);
	/* 100%: always changes. */
	CHECK(ChangeInflationRate(50, 30, 80, 100, Rnd(65535, 0)) != 50);
}

TEST_CASE("ChangeInflationRate - steps of 0.5%")
{
	CHECK(ChangeInflationRate(50, 30, 80, 100, Rnd(0, 0)) == 55);
	CHECK(ChangeInflationRate(50, 30, 80, 100, Rnd(0, 65535)) == 45);
}

TEST_CASE("ChangeInflationRate - direction depends on distance to the limits")
{
	/* At the minimum it always goes up, at the maximum always down. */
	for (uint32_t roll : {0u, 30000u, 65535u}) {
		CHECK(ChangeInflationRate(30, 30, 80, 100, Rnd(0, roll)) == 35);
		CHECK(ChangeInflationRate(80, 30, 80, 100, Rnd(0, roll)) == 75);
	}

	/* Count up-moves over all direction rolls: probability of up = (max - rate) / (max - min). */
	auto up_share = [](uint16_t rate) {
		uint up = 0;
		for (uint32_t roll = 0; roll < 65536; roll++) {
			if (ChangeInflationRate(rate, 30, 80, 100, Rnd(0, roll)) > rate) up++;
		}
		return up;
	};
	/* Exact shares of 65536 rolls, up to rounding of one roll. */
	auto near = [](uint value, uint expected) { return value + 1 >= expected && value <= expected + 1; };
	CHECK(near(up_share(55), 65536 / 2));
	CHECK(near(up_share(40), 65536 * 4 / 5));
	CHECK(near(up_share(70), 65536 / 5));
}

TEST_CASE("ChangeInflationRate - stays within the limits")
{
	/* A step never leaves the range. */
	CHECK(ChangeInflationRate(32, 30, 80, 100, Rnd(0, 65535)) >= 30);
	CHECK(ChangeInflationRate(78, 30, 80, 100, Rnd(0, 0)) <= 80);
	/* A rate outside the range (e.g. after a settings change) is clamped first. */
	CHECK(ChangeInflationRate(100, 30, 80, 0, Rnd(0, 0)) == 80);
	CHECK(ChangeInflationRate(10, 30, 80, 0, Rnd(0, 0)) == 30);
	/* Min above max: max is treated as min. */
	CHECK(ChangeInflationRate(50, 60, 40, 100, Rnd(0, 0)) == 60);
	/* Equal limits: fixed rate. */
	CHECK(ChangeInflationRate(50, 40, 40, 100, Rnd(0, 0)) == 40);
}

TEST_CASE("ApplyMonthlyInflation")
{
	/* Whole percent rates give the same result as the original formula. */
	for (uint64_t infl : {uint64_t{1} << 16, uint64_t{3} << 15, uint64_t{123456789}}) {
		for (uint8_t pct : {0, 2, 4, 8}) {
			CHECK(ApplyMonthlyInflation(infl, pct * 10) == infl + ((infl * pct * 54) >> 16));
		}
	}
	/* Twelve months at 5% is about 5%. */
	uint64_t infl = 1 << 16;
	for (int i = 0; i < 12; i++) infl = ApplyMonthlyInflation(infl, 50);
	CHECK(infl > (1 << 16) * 1048 / 1000);
	CHECK(infl < (1 << 16) * 1052 / 1000);
}

TEST_CASE("CumulativeInflationPercentTenths")
{
	CHECK(CumulativeInflationPercentTenths(1 << 16) == 0);
	CHECK(CumulativeInflationPercentTenths(2 << 16) == 1000);
	CHECK(CumulativeInflationPercentTenths(3 << 15) == 500);
}

TEST_CASE("VariableInflation history keeps the last 16 years")
{
	VariableInflation vi;
	for (int32_t year = 1950; year < 1950 + 20; year++) {
		InflationHistoryEntry e;
		e.year = year;
		vi.AddHistory(e);
	}
	CHECK(vi.history_count == INFLATION_HISTORY_YEARS);
	CHECK(vi.history[0].year == 1954);
	CHECK(vi.history[INFLATION_HISTORY_YEARS - 1].year == 1969);
}
