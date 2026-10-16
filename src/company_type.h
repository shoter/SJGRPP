/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file company_type.h Types related to companies. */

#ifndef COMPANY_TYPE_H
#define COMPANY_TYPE_H

#include "core/base_bitset_type.hpp"
#include "core/pool_type.hpp"

struct CompanyIDTag : public PoolIDTraits<uint8_t, 28, 0xFF> {};
using CompanyID = PoolID<CompanyIDTag>;

/* 'Fake' companies used for networks */
static constexpr CompanyID COMPANY_INACTIVE_CLIENT{253}; ///< The client is joining
static constexpr CompanyID COMPANY_NEW_COMPANY{254}; ///< The client wants a new company
static constexpr CompanyID COMPANY_SPECTATOR{255}; ///< The client is spectating

using Owner = CompanyID;
static constexpr Owner OWNER_BEGIN = Owner::Begin(); ///< First owner
static constexpr Owner OWNER_TOWN{0x1C}; ///< A town owns the tile, or a town is expanding
static constexpr Owner OWNER_NONE{0x1D}; ///< The tile has no ownership
static constexpr Owner OWNER_WATER{0x1E}; ///< The tile/execution is done by "water"
static constexpr Owner OWNER_DEITY{0x1F}; ///< The object is owned by a superuser / goal script
static constexpr Owner OWNER_END{0x20}; ///< Last + 1 owner
static constexpr Owner INVALID_OWNER = Owner::Invalid(); ///< An invalid owner

static const uint8_t MAX_COMPANIES = CompanyID::End().base();
static const uint8_t LEGACY_MAX_COMPANIES = 15; ///< Maximum number of companies before the limit was raised, used for savegame and NewGRF compatibility.
static_assert(OWNER_TOWN.base() == MAX_COMPANIES);

/**
 * Encode an owner in the legacy 5-bit form, as stored in the map array and as exposed to NewGRFs.
 * Companies 0..14 and the special owners keep their historic values (town 0x0F, none 0x10, water 0x11, deity 0x12),
 * companies 15..27 use the previously unused values 0x13..0x1F.
 * @param o Owner to encode.
 * @return Encoded owner.
 */
constexpr uint8_t EncodeLegacyOwner(Owner o)
{
	const uint8_t v = o.base();
	if (v < LEGACY_MAX_COMPANIES) return v;
	if (v < MAX_COMPANIES) return v + (OWNER_END.base() - OWNER_TOWN.base());
	if (v < OWNER_END.base()) return v - OWNER_TOWN.base() + LEGACY_MAX_COMPANIES;
	return v;
}

/**
 * Decode an owner from the legacy 5-bit form, see #EncodeLegacyOwner.
 * @param v Encoded owner.
 * @return Decoded owner.
 */
constexpr Owner DecodeLegacyOwner(uint8_t v)
{
	constexpr uint8_t num_special = OWNER_END.base() - OWNER_TOWN.base();
	if (v < LEGACY_MAX_COMPANIES) return Owner{v};
	if (v < LEGACY_MAX_COMPANIES + num_special) return Owner{static_cast<uint8_t>(v - LEGACY_MAX_COMPANIES + OWNER_TOWN.base())};
	if (v < OWNER_END.base()) return Owner{static_cast<uint8_t>(v - num_special)};
	return Owner{v};
}
static_assert(EncodeLegacyOwner(OWNER_TOWN) == 0x0F && EncodeLegacyOwner(OWNER_NONE) == 0x10 && EncodeLegacyOwner(OWNER_WATER) == 0x11 && EncodeLegacyOwner(OWNER_DEITY) == 0x12);
static_assert(EncodeLegacyOwner(Owner{14}) == 14 && EncodeLegacyOwner(Owner{15}) == 0x13 && EncodeLegacyOwner(Owner{MAX_COMPANIES - 1}) == 0x1F);
static_assert(DecodeLegacyOwner(EncodeLegacyOwner(Owner{20})) == Owner{20} && DecodeLegacyOwner(0x10) == OWNER_NONE && DecodeLegacyOwner(0xFF) == INVALID_OWNER);

/**
 * Convert an owner value from a savegame which predates the raised company limit.
 * In those savegames the special owners used the legacy values (town 0x0F, none 0x10, water 0x11, deity 0x12).
 * @param o Owner as loaded.
 * @return Owner in the current numbering.
 */
constexpr Owner ConvertPreMoreCompaniesOwner(Owner o)
{
	return o.base() < OWNER_END.base() ? DecodeLegacyOwner(o.base()) : o;
}
static const uint MAX_LENGTH_PRESIDENT_NAME_CHARS = 32; ///< The maximum length of a president name in characters including '\0'
static const uint MAX_LENGTH_COMPANY_NAME_CHARS   = 32; ///< The maximum length of a company name in characters including '\0'

static const uint MAX_HISTORY_QUARTERS            = 24; ///< The maximum number of quarters kept as performance's history
static const uint MAX_COMPANY_SHARE_OWNERS        =  4; ///< The maximum number of shares of a company that can be owned by another company.

static const uint MIN_COMPETITORS_INTERVAL = 0;   ///< The minimum interval (in minutes) between competitors.
static const uint MAX_COMPETITORS_INTERVAL = 500; ///< The maximum interval (in minutes) between competitors.

typedef Owner CompanyID;

class CompanyMask : public BaseBitSet<CompanyMask, CompanyID, uint32_t> {
public:
	constexpr CompanyMask() : BaseBitSet<CompanyMask, CompanyID, uint32_t>() {}
	static constexpr size_t DecayValueType(CompanyID value) { return value.base(); }

	constexpr auto operator <=>(const CompanyMask &) const noexcept = default;
};

struct Company;

struct CompanyManagerFace {
	uint style = 0; ///< Company manager face style.
	uint32_t bits = 0; ///< Company manager face bits, meaning is dependent on style.
	std::string style_label; ///< Face style label.
	std::string image; ///< Id of the chosen face image (see face_images.h); empty to pick one from #bits.
};

/** The reason why the company was removed. */
enum class CompanyRemoveReason : uint8_t {
	Manual, ///< The company is manually removed.
	Autoclean, ///< The company is removed due to autoclean.
	Bankrupt, ///< The company went belly-up.

	End, ///< Sentinel for end.

	None = Manual, ///< Dummy reason for actions that don't need one.
};

/** The action to do with Commands::CompanyControl. */
enum class CompanyCtrlAction : uint8_t {
	New,    ///< Create a new company.
	NewAI,  ///< Create a new AI company.
	Delete, ///< Delete a company.
	Sale,   ///< Offer a company for sale.
	Merge,  ///< Merge companies.
};

/** The action to do with Commands::CompanyAllowListControl. */
enum class CompanyAllowListCtrlAction : uint8_t {
	AddKey,      ///< Create a public key.
	RemoveKey,   ///< Remove a public key.
	AllowAny,    ///< Allow joining the company without a key.
	AllowListed, ///< Allow only listed keys to join the company.
};

struct CompanyInfoDumper {
	CompanyID value;
	CompanyInfoDumper(CompanyID value) : value(value) {}

	void fmt_format_value(struct format_target &output) const;
};

#endif /* COMPANY_TYPE_H */
