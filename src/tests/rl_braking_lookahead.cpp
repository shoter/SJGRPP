/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file rl_braking_lookahead.cpp Test the bound used to stop the realistic braking lookahead speed limit loop early. */

#include "../stdafx.h"

#include "../3rdparty/catch2/catch.hpp"

#include "../train.h"
#include "../pbs.h"
#include "../settings_type.h"

#include "../safeguards.h"

extern void LimitSpeedFromLookAhead(int &max_speed, const TrainDecelerationStats &stats, int current_position, int position, int end_speed, int z_delta);

/** Small deterministic pseudo random generator, so failures can be reproduced. */
struct TestRandom {
	uint32_t state;

	explicit TestRandom(uint32_t seed) : state(seed) {}

	/** @return A number in [lo, hi]. */
	int Next(int lo, int hi)
	{
		this->state = this->state * 1664525u + 1013904223u;
		return lo + static_cast<int>((this->state >> 8) % static_cast<uint32_t>(hi - lo + 1));
	}
};

static TrainReservationLookAhead MakeLookAhead(int32_t end_position)
{
	TrainReservationLookAhead lookahead{};
	lookahead.current_position = 0;
	lookahead.reservation_end_position = end_position;
	lookahead.lookahead_end_position = end_position;
	lookahead.next_extend_position = 0;
	lookahead.reservation_end_z = 0;
	lookahead.tunnel_bridge_reserved_tiles = 0;
	lookahead.speed_restriction = 0;
	return lookahead;
}

/** Check that the bounds are exactly the lowest z and the largest backwards step of item starts. */
static void CheckBoundsExact(TrainReservationLookAhead &lookahead)
{
	lookahead.UpdateItemsBounds();
	REQUIRE(lookahead.items_bounds_valid);
	int16_t min_z = INT16_MAX;
	int32_t dip = 0;
	for (size_t i = 0; i < lookahead.items.size(); i++) {
		min_z = std::min(min_z, lookahead.items[i].z_pos);
		for (size_t j = i + 1; j < lookahead.items.size(); j++) {
			dip = std::max(dip, lookahead.items[i].start - lookahead.items[j].start);
		}
	}
	CHECK(lookahead.items_min_z == min_z);
	CHECK(lookahead.items_start_dip == dip);
}

TEST_CASE("Lookahead item bounds - monotonic items")
{
	TrainReservationLookAhead lookahead = MakeLookAhead(100);
	lookahead.AddSignal(0, 4, 24, 0);
	lookahead.AddCurveSpeedLimit(80, 8, 16);
	lookahead.reservation_end_position = 132;
	lookahead.AddStation(3, StationID{1}, 40);
	lookahead.AddTrackSpeedLimit(120, 20, 4, 8);
	lookahead.UpdateItemsBounds();
	CHECK(lookahead.items_min_z == 8);
	CHECK(lookahead.items_start_dip == 0);
	CheckBoundsExact(lookahead);
}

TEST_CASE("Lookahead item bounds - adding, removing and moving items invalidates them")
{
	TrainReservationLookAhead lookahead = MakeLookAhead(0);
	lookahead.UpdateItemsBounds();
	CHECK(lookahead.items_bounds_valid);
	CHECK(lookahead.items_start_dip == 0);

	/* An item starting before an earlier one: start 16 after one at 30. */
	lookahead.AddTrackSpeedLimit(100, 30, 4, 0);
	CHECK_FALSE(lookahead.items_bounds_valid);
	lookahead.AddSpeedRestriction(60, 16, 4, -8);
	CHECK_FALSE(lookahead.items_bounds_valid);
	lookahead.UpdateItemsBounds();
	CHECK(lookahead.items_start_dip == 14);
	CHECK(lookahead.items_min_z == -8);

	/* Station platform behind the train: start moved back after adding, as in the lookahead creation. */
	lookahead.AddStation(2, StationID{1}, 0);
	lookahead.items.back().start -= 64;
	lookahead.InvalidateItemsBounds();
	CheckBoundsExact(lookahead);
	CHECK(lookahead.items_start_dip == 30 + 64);

	/* Removing the earlier items tightens the bounds again. */
	lookahead.items.pop_front();
	lookahead.items.pop_front();
	lookahead.InvalidateItemsBounds();
	CheckBoundsExact(lookahead);
	CHECK(lookahead.items_start_dip == 0);
	CHECK(lookahead.items_min_z == 0);

	lookahead.items.pop_front();
	lookahead.InvalidateItemsBounds();
	CheckBoundsExact(lookahead);
}

TEST_CASE("Lookahead item bounds - random item sequences")
{
	TestRandom rnd(12345);
	for (int round = 0; round < 200; round++) {
		TrainReservationLookAhead lookahead = MakeLookAhead(rnd.Next(-200, 200));
		const int steps = rnd.Next(1, 60);
		for (int step = 0; step < steps; step++) {
			const int16_t z = static_cast<int16_t>(rnd.Next(-50, 400));
			const int offset = rnd.Next(0, 48);
			switch (rnd.Next(0, 9)) {
				case 0: lookahead.AddStation(rnd.Next(1, 8), StationID{1}, z); break;
				case 1: lookahead.AddReverse(z); break;
				case 2: lookahead.AddTrackSpeedLimit(100, offset, 4, z); break;
				case 3: lookahead.AddSpeedRestriction(60, offset, 4, z); break;
				case 4: lookahead.AddSignal(0, offset, z, 0); break;
				case 5: lookahead.AddCurveSpeedLimit(80, offset, z); break;
				case 6: lookahead.AddSpeedAdaptation(TileIndex{0}, 0, offset, z); break;
				case 7:
					if (!lookahead.items.empty()) {
						lookahead.items.pop_front();
						lookahead.InvalidateItemsBounds();
					}
					break;
				case 8:
					if (!lookahead.items.empty()) {
						lookahead.items.back().start -= rnd.Next(0, 100);
						lookahead.InvalidateItemsBounds();
					}
					break;
				case 9:
					lookahead.reservation_end_position += rnd.Next(0, 64);
					break;
			}
			if (rnd.Next(0, 3) == 0) CheckBoundsExact(lookahead);
		}
		CheckBoundsExact(lookahead);
	}
}

TEST_CASE("Lookahead cutoff position - flat")
{
	_settings_game.vehicle.train_acceleration_model = AccelerationModel::Realistic;
	_settings_game.vehicle.train_slope_steepness = 3;

	/* Braking from 160 to 0 with deceleration_x2 = 20: 160^2 / 20 = 1280. */
	const TrainDecelerationStats stats(20, 40, 100);
	CHECK(GetLookAheadItemCutoffPosition(stats, 1000, 160, 100, 0) == 1000 + 1280 + LOOKAHEAD_ITEM_POSITION_SLACK);
	/* Items above the train do not lengthen the braking distance. */
	CHECK(GetLookAheadItemCutoffPosition(stats, 1000, 160, 150, 0) == 1000 + 1280 + LOOKAHEAD_ITEM_POSITION_SLACK);
	/* Items starting before earlier items move the cutoff back. */
	CHECK(GetLookAheadItemCutoffPosition(stats, 1000, 160, 100, 37) == 1000 + 1280 + 37 + LOOKAHEAD_ITEM_POSITION_SLACK);
	/* Lower speed, shorter distance. */
	CHECK(GetLookAheadItemCutoffPosition(stats, 1000, 40, 100, 0) == 1000 + 80 + LOOKAHEAD_ITEM_POSITION_SLACK);
}

TEST_CASE("Lookahead cutoff position - descending")
{
	_settings_game.vehicle.train_acceleration_model = AccelerationModel::Realistic;
	_settings_game.vehicle.train_slope_steepness = 3;

	/* Items 40 below the train: (160^2 + 40 * 111 * 3) / 40 = 973 is less than 1280 from the capped deceleration. */
	const TrainDecelerationStats stats(20, 40, 100);
	CHECK(GetLookAheadItemCutoffPosition(stats, 0, 160, 60, 0) == 1280 + LOOKAHEAD_ITEM_POSITION_SLACK);
	/* Items 400 below the train: (160^2 + 400 * 111 * 3) / 40 = 3970. */
	CHECK(GetLookAheadItemCutoffPosition(stats, 0, 160, -300, 0) == 3970 + LOOKAHEAD_ITEM_POSITION_SLACK);

	/* The original acceleration model has no slope term. */
	_settings_game.vehicle.train_acceleration_model = AccelerationModel::Original;
	CHECK(GetLookAheadItemCutoffPosition(stats, 0, 160, -300, 0) == 1280 + LOOKAHEAD_ITEM_POSITION_SLACK);
	_settings_game.vehicle.train_acceleration_model = AccelerationModel::Realistic;
}

/**
 * For any speed limit which ApplyLookAheadItem can apply for an item beyond the cutoff (position at most
 * LOOKAHEAD_ITEM_POSITION_SLACK before the item start, end speed at least 0, z at least the lowest item z, start speed at
 * most the max speed), the braking distance must not reach the position, so LimitSpeedFromLookAhead cannot change the speed.
 */
TEST_CASE("Lookahead cutoff position - no speed limit beyond it can lower the speed")
{
	TestRandom rnd(777);
	for (AccelerationModel model : { AccelerationModel::Original, AccelerationModel::Realistic }) {
		_settings_game.vehicle.train_acceleration_model = model;
		for (int round = 0; round < 20000; round++) {
			_settings_game.vehicle.train_slope_steepness = static_cast<uint8_t>(rnd.Next(0, 10));
			const int decel_x2 = 2 * rnd.Next(1, 200);
			const int udecel_x2 = std::max(decel_x2, 2 * rnd.Next(1, 2000));
			const int train_z = rnd.Next(0, 2000);
			const TrainDecelerationStats stats(decel_x2, udecel_x2, train_z);
			const int current_position = rnd.Next(-1000, 100000);
			const int max_speed = rnd.Next(0, 1000);
			const int min_z = rnd.Next(0, 2000);
			const int dip = rnd.Next(0, 3) == 0 ? rnd.Next(0, 200) : 0;

			const int64_t cutoff = GetLookAheadItemCutoffPosition(stats, current_position, max_speed, min_z, dip);
			REQUIRE(cutoff > current_position);

			/* The first item beyond the cutoff, or a later item which starts up to dip before it. */
			const int64_t first_start = cutoff + 1 + (rnd.Next(0, 1) == 0 ? 0 : rnd.Next(0, 1000));
			const int64_t start = first_start - rnd.Next(0, dip);
			const int position = static_cast<int>(start - rnd.Next(0, LOOKAHEAD_ITEM_POSITION_SLACK - 1));
			const int speed = rnd.Next(0, 1) == 0 ? max_speed : rnd.Next(0, max_speed);
			const int end_speed = rnd.Next(0, 1) == 0 ? 0 : rnd.Next(0, max_speed + 10);
			const int z_delta = rnd.Next(min_z, min_z + 500) - train_z;

			REQUIRE(position > current_position);
			if (end_speed < speed) {
				INFO("round " << round << " speed " << speed << " end " << end_speed << " z_delta " << z_delta);
				REQUIRE(GetRealisticBrakingDistanceForSpeed(stats, speed, end_speed, z_delta) + current_position <= position);
			}
			int limited_speed = speed;
			LimitSpeedFromLookAhead(limited_speed, stats, current_position, position, end_speed, z_delta);
			CHECK(limited_speed == speed);
		}
	}
	_settings_game.vehicle.train_acceleration_model = AccelerationModel::Realistic;
}
