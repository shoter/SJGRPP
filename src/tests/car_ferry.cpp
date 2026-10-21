/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file car_ferry.cpp Test the order and placement rules of car ferries. */

#include "../stdafx.h"

#include "../3rdparty/catch2/catch.hpp"

#include "../car_ferry.h"
#include "../economy_func.h"
#include "../infrastructure_func.h"
#include "../settings_type.h"

#include "../safeguards.h"

static const StationID A{1};
static const StationID B{2};
static const StationID C{3};
static const FerryOrderStop NOT_A_STATION{StationID::Invalid(), FerryAction::None};

TEST_CASE("FindFerryLeaveOrder - next leave order after boarding")
{
	const std::vector<FerryOrderStop> orders = {
		{A, FerryAction::Board},
		{B, FerryAction::Leave},
		{C, FerryAction::None},
	};
	CHECK(FindFerryLeaveOrder(orders, 0) == 1u);
}

TEST_CASE("FindFerryLeaveOrder - skips other orders and wraps around")
{
	const std::vector<FerryOrderStop> orders = {
		{B, FerryAction::Leave},
		NOT_A_STATION,
		{C, FerryAction::None},
		{A, FerryAction::Board},
		NOT_A_STATION,
	};
	CHECK(FindFerryLeaveOrder(orders, 3) == 0u);
}

TEST_CASE("FindFerryLeaveOrder - first of several leave orders")
{
	const std::vector<FerryOrderStop> orders = {
		{A, FerryAction::Board},
		{C, FerryAction::None},
		{B, FerryAction::Leave},
		{C, FerryAction::Leave},
	};
	CHECK(FindFerryLeaveOrder(orders, 0) == 2u);
	/* Boarding at the second leave order: the next one is found after wrapping, not the order itself. */
	CHECK(FindFerryLeaveOrder(orders, 2) == 3u);
	CHECK(FindFerryLeaveOrder(orders, 3) == 2u);
}

TEST_CASE("FindFerryLeaveOrder - no leave order")
{
	const std::vector<FerryOrderStop> orders = {
		{A, FerryAction::Board},
		{B, FerryAction::None},
		{C, FerryAction::Board},
	};
	CHECK_FALSE(FindFerryLeaveOrder(orders, 0).has_value());
	CHECK_FALSE(FindFerryLeaveOrder(std::vector<FerryOrderStop>{{A, FerryAction::Board}}, 0).has_value());
	CHECK_FALSE(FindFerryLeaveOrder(std::vector<FerryOrderStop>{}, 0).has_value());
}

TEST_CASE("FindFerryLeaveOrder - a non-station order cannot be a leave order")
{
	const std::vector<FerryOrderStop> orders = {
		{A, FerryAction::Board},
		{StationID::Invalid(), FerryAction::Leave},
	};
	CHECK_FALSE(FindFerryLeaveOrder(orders, 0).has_value());
}

TEST_CASE("FerryRouteCallsAt")
{
	const std::vector<FerryOrderStop> ship = {
		{A, FerryAction::None},
		NOT_A_STATION,
		{B, FerryAction::None},
	};
	CHECK(FerryRouteCallsAt(ship, A));
	CHECK(FerryRouteCallsAt(ship, B));
	CHECK_FALSE(FerryRouteCallsAt(ship, C));
	CHECK_FALSE(FerryRouteCallsAt(ship, StationID::Invalid()));
	CHECK_FALSE(FerryRouteCallsAt(std::vector<FerryOrderStop>{}, A));
}

TEST_CASE("GetFerryFreeSpaces - one road vehicle per unit of capacity")
{
	CHECK(GetFerryFreeSpaces(200, 0) == 200);
	CHECK(GetFerryFreeSpaces(200, 199) == 1);
	CHECK(GetFerryFreeSpaces(200, 200) == 0);
	CHECK(GetFerryFreeSpaces(200, 250) == 0);
	CHECK(GetFerryFreeSpaces(0, 0) == 0);
}

TEST_CASE("GetFerrySpawnFrames - single vehicle starts at the tile edge")
{
	const std::vector<uint> lengths = {8};
	auto frames = GetFerrySpawnFrames(lengths, 15);
	REQUIRE(frames.has_value());
	CHECK(*frames == std::vector<uint>{0});
}

TEST_CASE("GetFerrySpawnFrames - articulated parts follow one part length apart")
{
	const std::vector<uint> lengths = {8, 8};
	auto frames = GetFerrySpawnFrames(lengths, 15);
	REQUIRE(frames.has_value());
	CHECK(*frames == std::vector<uint>{8, 0});

	const std::vector<uint> three = {6, 4, 5};
	frames = GetFerrySpawnFrames(three, 15);
	REQUIRE(frames.has_value());
	CHECK(*frames == std::vector<uint>{10, 4, 0});
}

TEST_CASE("GetFerrySpawnFrames - vehicle must fit on one tile")
{
	CHECK(GetFerrySpawnFrames(std::vector<uint>{8, 7, 1}, 15).has_value());
	CHECK_FALSE(GetFerrySpawnFrames(std::vector<uint>{8, 8, 1}, 15).has_value());
	/* The length of the last part does not matter, it starts at the edge. */
	CHECK(GetFerrySpawnFrames(std::vector<uint>{15, 16}, 15).has_value());
	CHECK_FALSE(GetFerrySpawnFrames(std::vector<uint>{}, 15).has_value());
}

/** Sets the infrastructure sharing settings for one test and restores them afterwards. */
struct SharingSettings {
	EconomySettings saved_settings = _settings_game.economy;
	uint64_t saved_inflation = _economy.inflation_prices;

	SharingSettings(bool road, bool ship)
	{
		std::fill(std::begin(_settings_game.economy.infrastructure_sharing), std::end(_settings_game.economy.infrastructure_sharing), false);
		_settings_game.economy.infrastructure_sharing[to_underlying(VehicleType::Road)] = road;
		_settings_game.economy.infrastructure_sharing[to_underlying(VehicleType::Ship)] = ship;
	}

	~SharingSettings()
	{
		_settings_game.economy = this->saved_settings;
		_economy.inflation_prices = this->saved_inflation;
	}
};

static const Owner COMPANY_A{0};
static const Owner COMPANY_B{1};

TEST_CASE("CanBoardCarFerryOf - own car ferries always")
{
	SharingSettings sharing(false, false);
	CHECK(CanBoardCarFerryOf(COMPANY_A, COMPANY_A));
	CHECK(CanBoardCarFerryOf(COMPANY_B, COMPANY_B));
}

TEST_CASE("CanBoardCarFerryOf - another company's car ferry only with ship sharing")
{
	{
		SharingSettings sharing(false, false);
		CHECK_FALSE(CanBoardCarFerryOf(COMPANY_B, COMPANY_A));
	}
	{
		/* Sharing road stops is not enough: the road vehicle uses the other company's ship. */
		SharingSettings sharing(true, false);
		CHECK_FALSE(CanBoardCarFerryOf(COMPANY_B, COMPANY_A));
	}
	{
		SharingSettings sharing(false, true);
		CHECK(CanBoardCarFerryOf(COMPANY_B, COMPANY_A));
		CHECK(CanBoardCarFerryOf(COMPANY_A, COMPANY_B));
	}
}

TEST_CASE("GetCarFerrySharingFeeFract - one day of the ship sharing fee per boarding")
{
	SharingSettings sharing(false, true);
	_settings_game.economy.sharing_fee[to_underlying(VehicleType::Ship)] = 300;
	_settings_game.economy.sharing_fee[to_underlying(VehicleType::Road)] = 7;
	_settings_game.economy.sharing_fee_inflation = true;
	_economy.inflation_prices = 1 << 16;

	CHECK(GetCarFerrySharingFeeFract(COMPANY_B, COMPANY_A) == 300 << 8);
	/* Own car ferries are free. */
	CHECK(GetCarFerrySharingFeeFract(COMPANY_A, COMPANY_A) == 0);

	/* The fee scales with inflation like the other sharing fees. */
	_economy.inflation_prices = 2 << 16;
	CHECK(GetCarFerrySharingFeeFract(COMPANY_B, COMPANY_A) == 600 << 8);
	_settings_game.economy.sharing_fee_inflation = false;
	CHECK(GetCarFerrySharingFeeFract(COMPANY_B, COMPANY_A) == 300 << 8);

	_settings_game.economy.sharing_fee[to_underlying(VehicleType::Ship)] = 0;
	CHECK(GetCarFerrySharingFeeFract(COMPANY_B, COMPANY_A) == 0);
}
