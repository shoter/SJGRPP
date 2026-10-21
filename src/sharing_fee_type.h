/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file sharing_fee_type.h Types of the infrastructure sharing fees that companies set for their own infrastructure. */

#ifndef SHARING_FEE_TYPE_H
#define SHARING_FEE_TYPE_H

#include "economy_type.h"
#include "date_type.h"

/** Infrastructure sharing fee that a company charges others for one vehicle type. */
struct CompanySharingFee {
	bool custom = false;           ///< The company charges its own fee instead of the server's fee.
	Money fee = 0;                 ///< Own fee as a fixed amount, per day (trains: per tile of train length per day). Only used if #custom.
	bool pending = false;          ///< An increase was announced and is not in effect yet.
	bool pending_custom = false;   ///< #custom after the pending change.
	Money pending_fee = 0;         ///< #fee after the pending change.
	EconTime::Date pending_date{}; ///< Economy date on which the pending change takes effect.
};

#endif /* SHARING_FEE_TYPE_H */
