/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file variable_inflation.h Inflation rates that change from year to year, and their history. */

#ifndef VARIABLE_INFLATION_H
#define VARIABLE_INFLATION_H

#include <array>

/** Step by which an inflation rate changes at the start of a year, in tenths of a percent. */
static constexpr uint16_t INFLATION_RATE_STEP = 5;

/** Number of years of inflation history that are kept. */
static constexpr uint INFLATION_HISTORY_YEARS = 16;

/** Inflation state of one year. */
struct InflationHistoryEntry {
	int32_t year = 0;                ///< Calendar year this entry is for.
	uint16_t cost_rate = 0;          ///< Cost inflation rate during the year, in tenths of a percent.
	uint16_t income_rate = 0;        ///< Income inflation rate during the year, in tenths of a percent.
	uint64_t inflation_prices = 0;   ///< Cumulative cost inflation at the start of the year; 16 bit fractional part.
	uint64_t inflation_payment = 0;  ///< Cumulative income inflation at the start of the year; 16 bit fractional part.
};

/** Current inflation rates and their history. */
struct VariableInflation {
	uint16_t cost_rate = 0;   ///< Current cost inflation rate, in tenths of a percent per year.
	uint16_t income_rate = 0; ///< Current income inflation rate, in tenths of a percent per year.
	std::array<InflationHistoryEntry, INFLATION_HISTORY_YEARS> history{}; ///< History, oldest first.
	uint8_t history_count = 0; ///< Number of valid entries in #history.

	void AddHistory(const InflationHistoryEntry &entry);
};

extern VariableInflation _variable_inflation;

uint16_t ChangeInflationRate(uint16_t rate, uint16_t min_rate, uint16_t max_rate, uint chance, uint32_t random);
uint64_t ApplyMonthlyInflation(uint64_t inflation, uint16_t rate);
int64_t CumulativeInflationPercentTenths(uint64_t inflation);

void InitVariableInflation();
void RecordInflationHistory();
void VariableInflationYearlyLoop();

#endif /* VARIABLE_INFLATION_H */
