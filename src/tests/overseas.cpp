/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file overseas.cpp Test the trip times, prices, production and flags of overseas stations. */

#include "../stdafx.h"

#include "../3rdparty/catch2/catch.hpp"

#include "../overseas.h"
#include "../overseas_flag.h"

#include <set>

#include "../safeguards.h"

TEST_CASE("Overseas countries - 60 countries without the home country")
{
	auto countries = GetOverseasCountries();
	CHECK(countries.size() == 60);

	std::set<StringID> names;
	for (const OverseasCountry &c : countries) {
		names.insert(c.name);
		CHECK(c.population >= 20);
		CHECK(c.distance_km > 0);
		CHECK(IsValidOverseasFlag(c.flag));
	}
	CHECK(names.size() == countries.size());

	/* Poland is the home country; the nearest country (Germany) is about 500 km away, the farthest (Australia) about 16000 km. */
	auto [nearest, farthest] = std::ranges::minmax(countries, {}, &OverseasCountry::distance_km);
	CHECK(nearest.distance_km >= 400);
	CHECK(farthest.distance_km >= 15000);
}

TEST_CASE("Overseas trip time - linear between the nearest and the farthest country")
{
	CHECK(GetOverseasTripDaysForDistance(500, 500, 16000, 5, 60) == 5);
	CHECK(GetOverseasTripDaysForDistance(16000, 500, 16000, 5, 60) == 60);
	CHECK(GetOverseasTripDaysForDistance(8250, 500, 16000, 5, 60) == 33); // halfway: 5 + 27.5 rounded
	/* Out of range distances are clamped, swapped limits are sorted, equal distances take the shortest time. */
	CHECK(GetOverseasTripDaysForDistance(100, 500, 16000, 5, 60) == 5);
	CHECK(GetOverseasTripDaysForDistance(20000, 500, 16000, 5, 60) == 60);
	CHECK(GetOverseasTripDaysForDistance(16000, 500, 16000, 60, 5) == 60);
	CHECK(GetOverseasTripDaysForDistance(700, 700, 700, 5, 60) == 5);
}

TEST_CASE("Overseas trip time - aircraft take a percentage of the ship time")
{
	CHECK(GetOverseasAircraftTripDays(60, 25) == 15);
	CHECK(GetOverseasAircraftTripDays(22, 50) == 11);
	CHECK(GetOverseasAircraftTripDays(10, 25) == 3); // 2.5 rounded
	CHECK(GetOverseasAircraftTripDays(5, 1) == 1);   // at least one day
	CHECK(GetOverseasAircraftTripDays(40, 100) == 40);
}

TEST_CASE("Overseas virtual distance - one tile per 50 km, at least 8")
{
	CHECK(GetOverseasVirtualDistance(5240) == 104);
	CHECK(GetOverseasVirtualDistance(15800) == 316);
	CHECK(GetOverseasVirtualDistance(100) == 8);
}

TEST_CASE("Overseas control price - fee times (X+1)!")
{
	CHECK(GetOverseasControlBasePrice(100000, 0) == 100000);
	CHECK(GetOverseasControlBasePrice(100000, 1) == 200000);
	CHECK(GetOverseasControlBasePrice(100000, 2) == 600000);
	CHECK(GetOverseasControlBasePrice(100000, 3) == 2400000);
	CHECK(GetOverseasControlBasePrice(100000, 4) == 12000000);
	CHECK(GetOverseasControlBasePrice(0, 5) == 0);
	/* 60! does not fit: the price saturates instead of overflowing. */
	Money huge = GetOverseasControlBasePrice(100000, 59);
	CHECK(huge > 0);
	CHECK(huge >= GetOverseasControlBasePrice(100000, 20));
}

TEST_CASE("Overseas production - scaled by the square root of the population")
{
	CHECK(GetOverseasPopulationScale(100) == 256);
	CHECK(GetOverseasPopulationScale(400) == 512);
	CHECK(GetOverseasPopulationScale(1441) == 972);
	CHECK(GetOverseasPopulationScale(22) == 128);   // at least half
	CHECK(GetOverseasPopulationScale(5000) == 1024); // at most four times

	CHECK(GetOverseasMonthlyProduction(400, 100, OVERSEAS_GROWTH_UNIT, false) == 400);
	CHECK(GetOverseasMonthlyProduction(400, 400, OVERSEAS_GROWTH_UNIT, false) == 800);
	CHECK(GetOverseasMonthlyProduction(400, 100, OVERSEAS_GROWTH_UNIT * 2, false) == 800);
	CHECK(GetOverseasMonthlyProduction(0, 1441, OVERSEAS_GROWTH_UNIT, false) == 0);
}

TEST_CASE("Overseas production - halved during a recession")
{
	CHECK(GetOverseasMonthlyProduction(400, 100, OVERSEAS_GROWTH_UNIT, true) == 200);
	CHECK(GetOverseasMonthlyProduction(401, 100, OVERSEAS_GROWTH_UNIT, true) == 201);
}

TEST_CASE("Overseas production - grows every year")
{
	uint32_t growth = OVERSEAS_GROWTH_UNIT;
	for (int year = 0; year < 10; year++) growth = GrowOverseasProduction(growth, 1);
	/* 1.01^10 = 1.1046 */
	CHECK(growth > OVERSEAS_GROWTH_UNIT * 1.10);
	CHECK(growth < OVERSEAS_GROWTH_UNIT * 1.11);
	CHECK(GetOverseasMonthlyProduction(1000, 100, growth, false) == 1105);

	CHECK(GrowOverseasProduction(OVERSEAS_GROWTH_UNIT, 0) == OVERSEAS_GROWTH_UNIT);
	CHECK(GrowOverseasProduction(OVERSEAS_GROWTH_UNIT, 20) == OVERSEAS_GROWTH_UNIT * 6 / 5);
	/* Growth stops at 1000 times. */
	CHECK(GrowOverseasProduction(1000 * OVERSEAS_GROWTH_UNIT, 20) == 1000 * OVERSEAS_GROWTH_UNIT);
}

TEST_CASE("Overseas stockpile - months of production")
{
	CHECK(GetOverseasStockpileLimit(247, 6) == 1482);
	CHECK(GetOverseasStockpileLimit(0, 6) == 0);
}

TEST_CASE("Overseas flags - instructions are parsed and drawn")
{
	CHECK(IsValidOverseasFlag("H FF0000 FFFFFF"));
	CHECK(IsValidOverseasFlag("V 2:000000 FFFFFF; S 60 40 10 FFFF00 12"));
	CHECK_FALSE(IsValidOverseasFlag(""));
	CHECK_FALSE(IsValidOverseasFlag("H"));
	CHECK_FALSE(IsValidOverseasFlag("H FF00"));
	CHECK_FALSE(IsValidOverseasFlag("X 1 2 FFFFFF"));
	CHECK_FALSE(IsValidOverseasFlag("C 60 40 FFFFFF"));
	CHECK_FALSE(IsValidOverseasFlag("S 60 40 10 FFFFFF 2"));

	std::vector<uint32_t> px;
	REQUIRE(RenderOverseasFlag("H FF0000 FFFFFF", 30, 20, px));
	REQUIRE(px.size() == 600);
	CHECK(px[0] == 0xFFFF0000);           // top left: opaque red
	CHECK(px[19 * 30 + 29] == 0xFFFFFFFF); // bottom right: opaque white

	/* A disc leaves the corners transparent and fills the centre. */
	REQUIRE(RenderOverseasFlag("C 60 40 30 0000FF", 30, 20, px));
	CHECK(px[0] == 0);
	CHECK(px[10 * 30 + 15] == 0xFF0000FF);

	/* Later shapes are drawn over earlier ones. */
	REQUIRE(RenderOverseasFlag("R 0 0 120 80 FFFFFF; C 60 40 24 BC002D", 30, 20, px));
	CHECK(px[10 * 30 + 15] == 0xFFBC002D);
	CHECK(px[0] == 0xFFFFFFFF);
}
