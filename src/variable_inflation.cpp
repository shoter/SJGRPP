/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file variable_inflation.cpp Inflation rates that change from year to year, and their history. */

#include "stdafx.h"
#include "variable_inflation.h"
#include "economy_func.h"
#include "settings_type.h"
#include "date_func.h"
#include "core/random_func.hpp"
#include "window_func.h"

#include "safeguards.h"

VariableInflation _variable_inflation;

/**
 * Add an entry to the end of the history, dropping the oldest entry when the history is full.
 * @param entry The entry to add.
 */
void VariableInflation::AddHistory(const InflationHistoryEntry &entry)
{
	if (this->history_count == INFLATION_HISTORY_YEARS) {
		std::move(this->history.begin() + 1, this->history.end(), this->history.begin());
		this->history_count--;
	}
	this->history[this->history_count++] = entry;
}

/**
 * Compute the inflation rate for the next year.
 * The rate changes with the given chance by #INFLATION_RATE_STEP. The closer it is to the
 * minimum, the more likely it goes up; the closer to the maximum, the more likely it goes down.
 * @param rate Current rate, in tenths of a percent.
 * @param min_rate Minimum rate, in tenths of a percent.
 * @param max_rate Maximum rate, in tenths of a percent.
 * @param chance Chance of a change, in percent.
 * @param random Random bits: the low 16 decide whether the rate changes, the high 16 the direction.
 * @return The new rate, within [min_rate, max_rate].
 */
uint16_t ChangeInflationRate(uint16_t rate, uint16_t min_rate, uint16_t max_rate, uint chance, uint32_t random)
{
	if (max_rate < min_rate) max_rate = min_rate;
	rate = Clamp(rate, min_rate, max_rate);
	if (max_rate == min_rate) return rate;

	if (((GB(random, 0, 16) * 100U) >> 16) >= chance) return rate;

	/* Chance of going up: (max - rate) / (max - min). */
	uint span = max_rate - min_rate;
	bool up = ((GB(random, 16, 16) * span) >> 16) < static_cast<uint>(max_rate - rate);
	if (up) return static_cast<uint16_t>(std::min<uint>(rate + INFLATION_RATE_STEP, max_rate));
	return static_cast<uint16_t>(std::max<int>(rate - INFLATION_RATE_STEP, min_rate));
}

/**
 * Apply one month of inflation.
 * Approximation for (100 + rate)% ** (1 / 12) - 100%, scaled by 65536; only good for small rates.
 * @param inflation Cumulative inflation; 16 bit fractional part.
 * @param rate Yearly rate in tenths of a percent.
 * @return The new cumulative inflation.
 */
uint64_t ApplyMonthlyInflation(uint64_t inflation, uint16_t rate)
{
	return inflation + ((inflation * rate * 54 / 10) >> 16);
}

/**
 * Convert a cumulative inflation factor to the increase since the start, in tenths of a percent.
 * @param inflation Cumulative inflation; 16 bit fractional part.
 * @return Increase in tenths of a percent, e.g. 340 for prices 1.34 times the start.
 */
int64_t CumulativeInflationPercentTenths(uint64_t inflation)
{
	return ((static_cast<int64_t>(inflation) - (1 << 16)) * 1000) >> 16;
}

/** Set up the inflation rates of a new game from the old fixed rates. */
void InitVariableInflation()
{
	const EconomySettings &s = _settings_game.economy;
	uint16_t cost = _economy.infl_amount * 10;
	uint16_t income = _economy.infl_amount_pr * 10;
	if (s.inflation_change_chance > 0) {
		cost = Clamp<uint16_t>(cost, s.inflation_cost_min * 10, std::max(s.inflation_cost_min, s.inflation_cost_max) * 10);
		income = Clamp<uint16_t>(income, s.inflation_income_min * 10, std::max(s.inflation_income_min, s.inflation_income_max) * 10);
	}
	_variable_inflation = {};
	_variable_inflation.cost_rate = cost;
	_variable_inflation.income_rate = income;
}

/** Record the inflation state at the start of the current year. */
void RecordInflationHistory()
{
	InflationHistoryEntry entry;
	entry.year = CalTime::CurYear().base();
	entry.cost_rate = _variable_inflation.cost_rate;
	entry.income_rate = _variable_inflation.income_rate;
	entry.inflation_prices = _economy.inflation_prices;
	entry.inflation_payment = _economy.inflation_payment;

	/* Replace an entry of the same year, e.g. when the game starts. */
	if (_variable_inflation.history_count > 0 && _variable_inflation.history[_variable_inflation.history_count - 1].year == entry.year) {
		_variable_inflation.history[_variable_inflation.history_count - 1] = entry;
	} else {
		_variable_inflation.AddHistory(entry);
	}
	InvalidateWindowData(WindowClass::EconomyGraph, 0);
}

/** At the start of each calendar year, possibly change the inflation rates, and record the history. */
void VariableInflationYearlyLoop()
{
	const EconomySettings &s = _settings_game.economy;
	if (!s.inflation) return;

	if (s.inflation_change_chance > 0) {
		_variable_inflation.cost_rate = ChangeInflationRate(_variable_inflation.cost_rate, s.inflation_cost_min * 10, s.inflation_cost_max * 10, s.inflation_change_chance, Random());
		_variable_inflation.income_rate = ChangeInflationRate(_variable_inflation.income_rate, s.inflation_income_min * 10, s.inflation_income_max * 10, s.inflation_change_chance, Random());
		_economy.infl_amount = static_cast<uint8_t>(_variable_inflation.cost_rate / 10);
		_economy.infl_amount_pr = static_cast<uint8_t>(_variable_inflation.income_rate / 10);
	}
	RecordInflationHistory();
}
