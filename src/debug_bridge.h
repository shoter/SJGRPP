/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/**
 * @file debug_bridge.h Localhost TCP control/inspection channel for automated testing and debugging.
 *
 * The bridge is off unless enabled, either with the environment variable OTTD_DEBUG_BRIDGE=<port>
 * or with the console command "debug_bridge <port>". It only ever listens on 127.0.0.1.
 *
 * The protocol is line based: each request is one line of text ("command arg1 arg2 ..."; arguments
 * may be double-quoted), each response is one line of JSON: {"ok":true,"result":...} or {"ok":false,"error":"..."}.
 * Send "help" for the command list. See docs/debug_harness.md and utils/debug_harness/ for the client tools.
 *
 * Exposing values for inspection from game code:
 *  - DEBUG_BRIDGE_WATCH("name", expression);   at namespace scope in any .cpp: evaluated on each "get"/"watches" request.
 *  - DebugBridge::Probe("name", value);         anywhere: records the latest value (and a hit counter) under that name.
 *                                               Costs a single branch when the bridge is not running.
 */

#ifndef DEBUG_BRIDGE_H
#define DEBUG_BRIDGE_H

#include "3rdparty/fmt/format.h"

#include <functional>
#include <string>
#include <string_view>

namespace DebugBridge {
	/**
	 * Whether the game runs under automation (OTTD_DEBUG_BRIDGE or OTTD_AUTOMATION is set).
	 * Modal OS dialogs (error boxes, crash dialog) are suppressed then, as nobody is there to click them.
	 */
	inline bool IsAutomation()
	{
		const char *bridge = getenv("OTTD_DEBUG_BRIDGE");
		const char *automation = getenv("OTTD_AUTOMATION");
		return (bridge != nullptr && *bridge != '\0') || (automation != nullptr && *automation != '\0');
	}

	extern bool _active; ///< Whether the bridge is running; do not modify directly.

	bool Start(uint16_t port);
	void Stop();
	void StartFromEnvironment();
	uint16_t GetPort();

	void Poll();

	/* Lockstep support, implemented by video drivers which can run game ticks on demand. */
	void SetLockstepSupported(bool supported);
	void SetLockstep(bool lockstep);
	bool IsLockstep();
	bool TakeLockstepTick();

	/* Output capture hooks. */
	void OnConsolePrint(std::string_view line);
	void OnDebugPrint(std::string_view category, int level, std::string_view line);

	/* Watches and probes. */
	using WatchFunc = std::function<std::string()>;
	void RegisterWatch(std::string name, WatchFunc func);
	void SetProbeValue(std::string_view name, std::string value);

	template <typename T>
	inline void Probe(std::string_view name, const T &value)
	{
		if (!_active) [[likely]] return;
		if constexpr (std::is_enum_v<T>) {
			SetProbeValue(name, fmt::format("{}", static_cast<std::underlying_type_t<T>>(value)));
		} else {
			SetProbeValue(name, fmt::format("{}", value));
		}
	}

	/** Helper for static registration of watches, see #DEBUG_BRIDGE_WATCH. */
	struct WatchRegistration {
		WatchRegistration(std::string name, WatchFunc func) { RegisterWatch(std::move(name), std::move(func)); }
	};
}

#define DEBUG_BRIDGE_CONCAT_INNER(a, b) a ## b
#define DEBUG_BRIDGE_CONCAT(a, b) DEBUG_BRIDGE_CONCAT_INNER(a, b)

/** Register a named watch expression, evaluated on demand by the debug bridge. Use at namespace scope. */
#define DEBUG_BRIDGE_WATCH(name, expr) \
	static DebugBridge::WatchRegistration DEBUG_BRIDGE_CONCAT(_debug_bridge_watch_, __COUNTER__)(name, []() -> std::string { return fmt::format("{}", (expr)); })

#endif /* DEBUG_BRIDGE_H */
