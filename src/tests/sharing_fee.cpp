/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file sharing_fee.cpp Test the scaling of infrastructure sharing fees with inflation and the track toll of trains. */

#include "../stdafx.h"

#include "../3rdparty/catch2/catch.hpp"

#include "../economy_type.h"
#include "../date_type.h"
#include "../tile_type.h"
#include "../infrastructure_func.h"

#include "../safeguards.h"

TEST_CASE("ScaleSharingFeeFract - without inflation scaling")
{
	CHECK(ScaleSharingFeeFract(100, 1 << 16, false) == 100 << 8);
	CHECK(ScaleSharingFeeFract(100, 3 << 16, false) == 100 << 8);
	CHECK(ScaleSharingFeeFract(0, 3 << 16, false) == 0);
}

TEST_CASE("ScaleSharingFeeFract - with inflation scaling")
{
	/* No inflation yet: same as the setting. */
	CHECK(ScaleSharingFeeFract(100, 1 << 16, true) == 100 << 8);
	/* Prices doubled. */
	CHECK(ScaleSharingFeeFract(100, 2 << 16, true) == 200 << 8);
	/* Prices up by 50%. */
	CHECK(ScaleSharingFeeFract(100, 3 << 15, true) == 150 << 8);
	/* Fractions of a currency unit are kept. */
	CHECK(ScaleSharingFeeFract(1, (1 << 16) + (1 << 15), true) == (1 << 8) + (1 << 7));
	CHECK(ScaleSharingFeeFract(0, 5 << 16, true) == 0);
	/* Largest fee and inflation do not overflow. */
	CHECK(ScaleSharingFeeFract(1000000, MAX_INFLATION, true) == static_cast<Money>((1000000ull * MAX_INFLATION) >> 8));
}

TEST_CASE("GetTrackTollFract - per tile of train length")
{
	const Money fee = 7400 << 8; // 100 per tick for each tile of train length
	/* A train running all day pays the fee for each tile of its length. */
	CHECK(GetTrackTollFract(fee, TILE_SIZE, DAY_TICKS) == fee);
	CHECK(GetTrackTollFract(fee, 2 * TILE_SIZE, DAY_TICKS) == 2 * fee);
	/* Locomotive and three standard wagons: 4 vehicles of half a tile. */
	CHECK(GetTrackTollFract(fee, 4 * 8, DAY_TICKS) == 2 * fee);
	/* Fractions of a tile count: 4.5 tiles. */
	CHECK(GetTrackTollFract(fee, 9 * 8, DAY_TICKS) == fee * 9 / 2);
	/* Very short vehicles. */
	CHECK(GetTrackTollFract(fee, 3, DAY_TICKS) == fee * 3 / TILE_SIZE);
}

TEST_CASE("GetTrackTollFract - only running ticks are paid")
{
	const Money fee = 7400 << 8;
	CHECK(GetTrackTollFract(fee, 2 * TILE_SIZE, 0) == 0);
	CHECK(GetTrackTollFract(fee, 2 * TILE_SIZE, 1) == 2 * 100 << 8);
	CHECK(GetTrackTollFract(fee, 2 * TILE_SIZE, DAY_TICKS / 2) == fee);
	CHECK(GetTrackTollFract(0, 2 * TILE_SIZE, DAY_TICKS) == 0);
	CHECK(GetTrackTollFract(fee, 0, DAY_TICKS) == 0);
}

TEST_CASE("GetTrackTollFract - largest values do not overflow")
{
	/* Largest fee at the largest inflation, longest possible train. */
	const Money fee = ScaleSharingFeeFract(1000000, MAX_INFLATION, true);
	const Money expected = ((fee * UINT16_MAX) / TILE_SIZE * DAY_TICKS) / DAY_TICKS;
	CHECK(GetTrackTollFract(fee, UINT16_MAX, DAY_TICKS) == expected);
	CHECK(expected > 0);
}
