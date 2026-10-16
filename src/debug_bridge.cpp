/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file debug_bridge.cpp Localhost TCP control/inspection channel for automated testing and debugging. */

#include "stdafx.h"
#include "debug_bridge.h"
#include "blitter/factory.hpp"
#include "command_type.h"
#include "company_func.h"
#include "console_func.h"
#include "date_func.h"
#include "debug.h"
#include "gfx_func.h"
#include "landscape.h"
#include "openttd.h"
#include "rev.h"
#include "screenshot.h"
#include "station_base.h"
#include "town.h"
#include "vehicle_base.h"
#include "video/video_driver.hpp"
#include "viewport_func.h"
#include "window_func.h"
#include "window_gui.h"
#include "zoom_func.h"
#include "network/network.h"
#include "network/network_func.h"
#include "network/core/os_abstraction.h"

#include "3rdparty/nlohmann/json.hpp"

#include <atomic>
#include <charconv>
#include <chrono>
#include <deque>
#include <filesystem>
#include <map>
#include <mutex>
#include <optional>

#include "safeguards.h"

using json = nlohmann::json;

json DebugBridgeReflectTypes();
json DebugBridgeReflectObject(std::string_view type, size_t id, const std::vector<std::string> &fields, bool schema, std::string &error);
json DebugBridgeReflectList(std::string_view type, bool all, std::string &error);
json DebugBridgeCommandList(std::string_view filter);
bool DebugBridgeBuildCommand(const std::vector<std::string> &args, size_t first, DynBaseCommandContainer &out, std::string &error);
bool DebugBridgeBuildRawCommand(const std::vector<std::string> &args, size_t first, DynBaseCommandContainer &out, std::string &error);
bool DebugBridgeBuildOrder(const std::vector<std::string> &args, size_t first, DynBaseCommandContainer &out, std::string &error);
std::optional<json> DebugBridgeSubmitCommand(const DynBaseCommandContainer &container, uint32_t seq, CompanyID company);
json DebugBridgeTestCommand(const DynBaseCommandContainer &container, CompanyID company);
json DebugBridgeEngineList(std::string_view type);
json DebugBridgeTiles(uint x, uint y, uint w, uint h);
std::vector<std::string> DebugBridgeWindowNames();
std::string DebugBridgeShowWindow(std::string_view name, int64_t arg);
std::optional<json> DebugBridgeTakeCommandResult(uint32_t seq);
void DebugBridgeForgetCommandResult(uint32_t seq);

extern std::atomic<bool> _exit_game;
extern std::string _full_screenshot_path;

namespace DebugBridge {

bool _active = false;

/** Error raised by a command handler; reported to the client as {"ok":false,"error":...}. */
struct BridgeError {
	std::string message;
};

using Clock = std::chrono::steady_clock;

/** A command whose reply is delayed until a condition holds. Returns the result once done, std::nullopt while still waiting. */
using DeferredOp = std::function<std::optional<json>()>;

struct Connection {
	SOCKET sock = INVALID_SOCKET;
	std::string in;
	std::string out;
	DeferredOp pending;
	bool closing = false;
};

struct LogLine {
	uint64_t seq;
	std::string source;
	std::string text;
};

struct ProbeValue {
	std::string value;
	uint64_t hits = 0;
	uint64_t tick = 0;
};

static SOCKET _listen_sock = INVALID_SOCKET;
static uint16_t _port = 0;
static std::vector<std::unique_ptr<Connection>> _connections;

static bool _lockstep_supported = false;
static bool _lockstep = false;
static uint64_t _lockstep_pending = 0;

static std::mutex _log_mutex;
static std::deque<LogLine> _log;
static uint64_t _log_seq = 0;
static constexpr size_t LOG_CAPACITY = 10000;

static std::vector<std::string> *_console_capture = nullptr;

static std::mutex _probes_mutex; ///< Probes may be set from worker threads.
static std::map<std::string, ProbeValue, std::less<>> _probes;

static std::map<std::string, WatchFunc> &GetWatches()
{
	static std::map<std::string, WatchFunc> watches;
	return watches;
}

/* ---- Registration and hooks ---- */

void RegisterWatch(std::string name, WatchFunc func)
{
	GetWatches()[std::move(name)] = std::move(func);
}

void SetProbeValue(std::string_view name, std::string value)
{
	std::lock_guard<std::mutex> lock(_probes_mutex);
	auto it = _probes.find(name);
	if (it == _probes.end()) it = _probes.emplace(std::string{name}, ProbeValue{}).first;
	it->second.value = std::move(value);
	it->second.hits++;
	it->second.tick = _tick_counter;
}

static void AddLogLine(std::string source, std::string_view text)
{
	std::lock_guard<std::mutex> lock(_log_mutex);
	_log.push_back({ ++_log_seq, std::move(source), std::string{text} });
	while (_log.size() > LOG_CAPACITY) _log.pop_front();
}

void OnConsolePrint(std::string_view line)
{
	if (_console_capture != nullptr) _console_capture->emplace_back(line);
	if (_active) AddLogLine("console", line);
}

void OnDebugPrint(std::string_view category, int level, std::string_view line)
{
	if (_active) AddLogLine(fmt::format("debug:{}:{}", category, level), line);
}

void SetLockstepSupported(bool supported)
{
	_lockstep_supported = supported;
	if (!supported) {
		_lockstep = false;
		_lockstep_pending = 0;
	}
}

void SetLockstep(bool lockstep)
{
	_lockstep = lockstep && _lockstep_supported;
	if (!_lockstep) _lockstep_pending = 0;
}

bool IsLockstep()
{
	return _lockstep;
}

bool TakeLockstepTick()
{
	if (_lockstep_pending == 0) return false;
	_lockstep_pending--;
	return true;
}

/* ---- Helpers ---- */

static std::vector<std::string> Tokenise(std::string_view line)
{
	std::vector<std::string> tokens;
	size_t i = 0;
	while (i < line.size()) {
		while (i < line.size() && (line[i] == ' ' || line[i] == '\t')) i++;
		if (i >= line.size()) break;
		std::string token;
		if (line[i] == '"') {
			i++;
			while (i < line.size() && line[i] != '"') {
				if (line[i] == '\\' && i + 1 < line.size()) i++;
				token += line[i++];
			}
			i++;
		} else {
			while (i < line.size() && line[i] != ' ' && line[i] != '\t') token += line[i++];
		}
		tokens.push_back(std::move(token));
	}
	return tokens;
}

static int64_t ParseInt(const std::string &str, const char *what)
{
	std::string_view view{str};
	bool negative = view.starts_with('-');
	if (negative) view.remove_prefix(1);
	int base = 10;
	if (view.starts_with("0x") || view.starts_with("0X")) {
		view.remove_prefix(2);
		base = 16;
	}
	int64_t value = 0;
	auto [ptr, ec] = std::from_chars(view.data(), view.data() + view.size(), value, base);
	if (!view.empty() && ec == std::errc{} && ptr == view.data() + view.size()) return negative ? -value : value;
	throw BridgeError{ fmt::format("invalid {}: '{}'", what, str) };
}

static const std::string &Arg(const std::vector<std::string> &args, size_t index, const char *what)
{
	if (index >= args.size()) throw BridgeError{ fmt::format("missing argument: {}", what) };
	return args[index];
}

static bool HasFlag(const std::vector<std::string> &args, size_t from, std::string_view flag)
{
	for (size_t i = from; i < args.size(); i++) {
		if (args[i] == flag) return true;
	}
	return false;
}

static void RequireScreen()
{
	if (!VideoDriver::GetInstance()->HasGUI() || BlitterFactory::GetCurrentBlitter()->GetScreenDepth() == 0) {
		throw BridgeError{ "this instance has no screen (dedicated/null video driver)" };
	}
}

static json RunConsole(std::string_view command)
{
	std::vector<std::string> output;
	std::vector<std::string> *prev = _console_capture;
	_console_capture = &output;
	IConsoleCmdExec(command);
	_console_capture = prev;
	return output;
}

/* ---- Input injection ---- */

static void MoveMouse(int x, int y)
{
	_cursor.in_window = true;
	_cursor.UpdateCursorPosition(x, y);
	HandleMouseEvents();
}

struct ModifierScope {
	bool old_ctrl = _ctrl_pressed;
	bool old_shift = _shift_pressed;

	ModifierScope(bool ctrl, bool shift)
	{
		_ctrl_pressed = ctrl;
		_shift_pressed = shift;
		if (ctrl != this->old_ctrl) HandleCtrlChanged();
	}

	~ModifierScope()
	{
		bool changed = _ctrl_pressed != this->old_ctrl;
		_ctrl_pressed = this->old_ctrl;
		_shift_pressed = this->old_shift;
		if (changed) HandleCtrlChanged();
	}
};

static void Click(int x, int y, bool right, bool ctrl, bool shift)
{
	ModifierScope mods(ctrl, shift);
	MoveMouse(x, y);
	if (right) {
		_right_button_down = true;
		_right_button_clicked = true;
		HandleMouseEvents();
		_right_button_down = false;
		HandleMouseEvents();
	} else {
		_left_button_down = true;
		HandleMouseEvents();
		_left_button_down = false;
		_left_button_clicked = false;
		HandleMouseEvents();
	}
}

static uint16_t ParseKey(const std::string &name, char32_t &unicode)
{
	static const std::map<std::string, uint16_t, std::less<>> named = {
		{ "esc", WKC_ESC }, { "escape", WKC_ESC }, { "backspace", WKC_BACKSPACE }, { "insert", WKC_INSERT },
		{ "delete", WKC_DELETE }, { "del", WKC_DELETE }, { "pageup", WKC_PAGEUP }, { "pagedown", WKC_PAGEDOWN },
		{ "end", WKC_END }, { "home", WKC_HOME }, { "left", WKC_LEFT }, { "up", WKC_UP }, { "right", WKC_RIGHT },
		{ "down", WKC_DOWN }, { "return", WKC_RETURN }, { "enter", WKC_RETURN }, { "tab", WKC_TAB },
		{ "space", WKC_SPACE }, { "backquote", WKC_BACKQUOTE }, { "pause", WKC_PAUSE },
	};
	unicode = 0;
	auto it = named.find(name);
	if (it != named.end()) {
		if (it->second == WKC_SPACE) unicode = ' ';
		return it->second;
	}
	if (name.size() >= 2 && (name[0] == 'f' || name[0] == 'F')) {
		int n = 0;
		auto [ptr, ec] = std::from_chars(name.data() + 1, name.data() + name.size(), n);
		if (ec == std::errc{} && ptr == name.data() + name.size() && n >= 1 && n <= 12) return static_cast<uint16_t>(WKC_F1 + n - 1);
	}
	if (name.size() == 1) {
		char c = name[0];
		unicode = static_cast<unsigned char>(c);
		if (c >= 'a' && c <= 'z') return static_cast<uint16_t>(c - 'a' + 'A');
		return static_cast<uint16_t>(static_cast<unsigned char>(c));
	}
	throw BridgeError{ fmt::format("unknown key: '{}'", name) };
}

/* ---- Window inspection ---- */

static json DescribeWindow(const Window *w)
{
	json j = {
		{ "class", static_cast<int>(w->window_class) },
		{ "number", w->window_number.base() },
		{ "left", w->left }, { "top", w->top }, { "width", w->width }, { "height", w->height },
		{ "source", fmt::format("{}:{}", w->window_desc.file, w->window_desc.line) },
	};
	if (w->window_desc.ini_key != nullptr) j["ini_key"] = w->window_desc.ini_key;
	return j;
}

static Window *FindWindowArg(const std::vector<std::string> &args, size_t index)
{
	WindowClass cls = static_cast<WindowClass>(ParseInt(Arg(args, index, "window class"), "window class"));
	WindowNumber number = static_cast<int32_t>(ParseInt(Arg(args, index + 1, "window number"), "window number"));
	Window *w = FindWindowById(cls, number);
	if (w == nullptr) throw BridgeError{ "no such window" };
	return w;
}

static json DescribeWidget(const Window *w, WidgetID index, const NWidgetBase *nwid)
{
	json j = {
		{ "index", index },
		{ "type", static_cast<int>(nwid->type) },
		{ "x", w->left + nwid->pos_x }, { "y", w->top + nwid->pos_y },
		{ "width", nwid->current_x }, { "height", nwid->current_y },
	};
	if (const NWidgetCore *core = dynamic_cast<const NWidgetCore *>(nwid); core != nullptr) {
		j["disabled"] = core->IsDisabled();
		j["lowered"] = core->IsLowered();
		StringID str = core->GetString();
		if (index >= 0 && str != STR_NULL && str != INVALID_STRING_ID) {
			j["string_id"] = str.base();
			j["text"] = w->GetWidgetString(index, str);
		}
	}
	return j;
}

static json DescribeWidgets(Window *w)
{
	json list = json::array();
	for (const auto &[index, nwid] : w->widget_lookup) {
		if (nwid == nullptr) continue;
		list.push_back(DescribeWidget(w, index, nwid));
	}

	/* Window frame widgets usually have no widget ID, so they are not in the lookup. */
	static const std::pair<WidgetType, const char *> frame_widgets[] = {
		{ WWT_CLOSEBOX, "closebox" }, { WWT_CAPTION, "caption" }, { WWT_DEBUGBOX, "debugbox" }, { WWT_SHADEBOX, "shadebox" },
		{ WWT_DEFSIZEBOX, "defsizebox" }, { WWT_STICKYBOX, "stickybox" }, { WWT_RESIZEBOX, "resizebox" },
	};
	if (w->nested_root != nullptr) {
		for (const auto &[type, role] : frame_widgets) {
			const NWidgetBase *nwid = w->nested_root->GetWidgetOfType(type);
			if (nwid == nullptr) continue;
			json j = DescribeWidget(w, nwid->GetIndex(), nwid);
			j["role"] = role;
			list.push_back(std::move(j));
		}
	}
	return list;
}

/* ---- Map/screen coordinates ---- */

static Point TileToScreen(uint tx, uint ty)
{
	const Window *w = GetMainWindow();
	if (w == nullptr || w->viewport == nullptr) throw BridgeError{ "no main viewport" };
	if (tx >= Map::SizeX() || ty >= Map::SizeY()) throw BridgeError{ "tile outside map" };
	const Viewport &vp = *w->viewport;
	int px = tx * TILE_SIZE + TILE_SIZE / 2;
	int py = ty * TILE_SIZE + TILE_SIZE / 2;
	Point pt = RemapCoords(px, py, GetSlopePixelZ(px, py));
	return { vp.left + UnScaleByZoom(pt.x - vp.virtual_left, vp.zoom), vp.top + UnScaleByZoom(pt.y - vp.virtual_top, vp.zoom) };
}

/* ---- Info ---- */

static const char *GameModeName()
{
	switch (_game_mode) {
		case GameMode::Menu: return "menu";
		case GameMode::Normal: return "normal";
		case GameMode::Editor: return "editor";
		case GameMode::Bootstrap: return "bootstrap";
		default: return "unknown";
	}
}

static json GetInfo()
{
	const auto &ymd = CalTime::CurYMD();
	json j = json::object();
	j["revision"] = std::string(_openttd_revision);
	j["game_mode"] = GameModeName();
	j["tick_counter"] = _tick_counter;
	j["scaled_tick_counter"] = static_cast<uint64_t>(_scaled_tick_counter);
	j["date"] = fmt::format("{:04}-{:02}-{:02}", ymd.year.base(), ymd.month + 1, ymd.day);
	j["paused"] = _pause_mode.Any();
	j["pause_mode"] = _pause_mode.base();
	j["game_speed"] = _game_speed;
	j["networking"] = _networking;
	j["network_server"] = _network_server;
	j["dedicated"] = _network_dedicated;
	j["client_id"] = static_cast<uint32_t>(_network_own_client_id);
	j["local_company"] = _local_company.base();
	j["video_driver"] = VideoDriver::GetInstance()->GetName();
	j["screen"] = { { "width", _screen.width }, { "height", _screen.height } };
	j["lockstep"] = _lockstep;
	j["lockstep_supported"] = _lockstep_supported;
	j["lockstep_pending"] = _lockstep_pending;
	if (_game_mode != GameMode::Menu && Map::SizeX() > 0) j["map"] = { { "x", Map::SizeX() }, { "y", Map::SizeY() } };
	return j;
}

static void RegisterBuiltinWatches()
{
	static bool done = false;
	if (done) return;
	done = true;
	RegisterWatch("game_mode", []() { return std::string(GameModeName()); });
	RegisterWatch("tick_counter", []() { return fmt::format("{}", _tick_counter); });
	RegisterWatch("scaled_tick_counter", []() { return fmt::format("{}", static_cast<uint64_t>(_scaled_tick_counter)); });
	RegisterWatch("pause_mode", []() { return fmt::format("{}", _pause_mode.base()); });
	RegisterWatch("game_speed", []() { return fmt::format("{}", _game_speed); });
	RegisterWatch("local_company", []() { return fmt::format("{}", _local_company.base()); });
	RegisterWatch("current_company", []() { return fmt::format("{}", _current_company.base()); });
	RegisterWatch("client_id", []() { return fmt::format("{}", static_cast<uint32_t>(_network_own_client_id)); });
	RegisterWatch("cursor", []() { return fmt::format("{},{}", _cursor.pos.x, _cursor.pos.y); });
	RegisterWatch("num_vehicles", []() { return fmt::format("{}", Vehicle::GetNumItems()); });
	RegisterWatch("num_stations", []() { return fmt::format("{}", BaseStation::GetNumItems()); });
	RegisterWatch("num_towns", []() { return fmt::format("{}", Town::GetNumItems()); });
}

/* ---- Command dispatch ---- */

static const char HELP_TEXT[] =
	"ping | help | info | quit | crash yes-really [segv]  (deliberate crash, to test crash tooling)\n"
	"console <command line>            run a console command, returns its output lines\n"
	"log [since_seq] [max]             console + debug output ring buffer (returns lines with seq numbers)\n"
	"wait_log <substring> [timeout_ms] [since_seq]   wait until a log line containing substring appears\n"
	"get <name>...  | watches          read watch expressions (DEBUG_BRIDGE_WATCH) / list all with values\n"
	"probes [prefix] | probe_clear     values recorded with DebugBridge::Probe()\n"
	"obj_types | objs <type> [all] | obj <type> <id> [field...] | obj_schema <type> <id>   saveload-based object inspection\n"
	"screenshot [path] [screen|viewport|world|minimap]   (screen = raw headless frame buffer)\n"
	"windows | widgets <class> <number> (frame widgets without ID are included with a 'role') | close_window <class> <number>\n"
	"move <x> <y> | click <x> <y> [right] [ctrl] [shift] | click_widget <class> <number> <widget> [right] [ctrl] [shift]\n"
	"drag <x1> <y1> <x2> <y2> [steps] | wheel <x> <y> <delta> | key <name|char> [ctrl] [shift] [alt] | text <string>\n"
	"tile_screen <tx> <ty> | screen_tile <x> <y>\n"
	"resize <w> <h>\n"
	"lockstep on|off | step <ticks> | wait_ticks <ticks> [timeout_ms] | wait_frames <frames>\n"
	"cmds [filter]                     game commands with their payload field types\n"
	"docmd [test] [as <company>] <command> <tile> <field>...   issue a game command as the local company (like the GUI does); replies once\n"
	"                                  executed; 'test' only runs the command's test (cost/error), nothing is built or sent\n"
	"docmd_hex [test] <command> <tile> <hex>    same, payload given in its network serialisation\n"
	"order [test] <vehicle> station|waypoint|depot <dest> [nonstop|stop_everywhere] [full_load|full_load_any|no_load] [unload|transfer|no_unload]\n"
	"engines [rail|road|ship|aircraft] buildable engines for the local company\n"
	"show [<window> [id]]                open a game window by name (no arguments: list names); id picks the n-th station/town/vehicle/company\n"
	"tiles [x y w h]                   per-tile type/height/slope/owner/subtype/road owner arrays (row-major) for route planning";

static json HandleCommand(Connection &conn, const std::vector<std::string> &args, std::string_view line)
{
	const std::string &cmd = args[0];

	if (cmd == "ping") return "pong";
	if (cmd == "help") return HELP_TEXT;
	if (cmd == "info") return GetInfo();

	if (cmd == "quit") {
		_exit_game = true;
		return "bye";
	}

	if (cmd == "crash") {
		/* Deliberately crash, to test crash log / collector tooling. */
		if (args.size() < 2 || args[1] != "yes-really") throw BridgeError{ "usage: crash yes-really [segv]" };
		if (HasFlag(args, 2, "segv")) {
			volatile int *volatile null_ptr = nullptr;
			*null_ptr = 0;
		}
		NOT_REACHED();
	}

	if (cmd == "console") {
		size_t pos = line.find("console");
		std::string_view rest = line.substr(pos + 7);
		while (!rest.empty() && rest.front() == ' ') rest.remove_prefix(1);
		if (rest.empty()) throw BridgeError{ "missing console command" };
		return RunConsole(rest);
	}

	if (cmd == "log") {
		uint64_t since = args.size() > 1 ? ParseInt(args[1], "since_seq") : 0;
		size_t max = args.size() > 2 ? ParseInt(args[2], "max") : 500;
		json lines = json::array();
		std::lock_guard<std::mutex> lock(_log_mutex);
		for (const LogLine &l : _log) {
			if (l.seq <= since) continue;
			lines.push_back({ { "seq", l.seq }, { "source", l.source }, { "text", l.text } });
			if (lines.size() >= max) break;
		}
		return { { "last_seq", _log_seq }, { "lines", lines } };
	}

	if (cmd == "wait_log") {
		std::string needle = Arg(args, 1, "substring");
		auto deadline = Clock::now() + std::chrono::milliseconds(args.size() > 2 ? ParseInt(args[2], "timeout_ms") : 10000);
		uint64_t since = args.size() > 3 ? ParseInt(args[3], "since_seq") : 0;
		conn.pending = [needle, deadline, since]() -> std::optional<json> {
			std::lock_guard<std::mutex> lock(_log_mutex);
			for (const LogLine &l : _log) {
				if (l.seq > since && l.text.find(needle) != std::string::npos) {
					return json{ { "seq", l.seq }, { "source", l.source }, { "text", l.text } };
				}
			}
			if (Clock::now() > deadline) throw BridgeError{ fmt::format("timeout waiting for log line containing '{}'", needle) };
			return std::nullopt;
		};
		return nullptr;
	}

	if (cmd == "get") {
		RegisterBuiltinWatches();
		json result = json::object();
		for (size_t i = 1; i < args.size(); i++) {
			auto it = GetWatches().find(args[i]);
			if (it != GetWatches().end()) {
				result[args[i]] = it->second();
			} else {
				std::lock_guard<std::mutex> lock(_probes_mutex);
				auto pit = _probes.find(args[i]);
				result[args[i]] = pit != _probes.end() ? json(pit->second.value) : json(nullptr);
			}
		}
		return result;
	}

	if (cmd == "watches") {
		RegisterBuiltinWatches();
		json result = json::object();
		for (const auto &[name, func] : GetWatches()) result[name] = func();
		return result;
	}

	if (cmd == "probes") {
		std::string prefix = args.size() > 1 ? args[1] : std::string{};
		json result = json::object();
		std::lock_guard<std::mutex> lock(_probes_mutex);
		for (const auto &[name, probe] : _probes) {
			if (!name.starts_with(prefix)) continue;
			result[name] = { { "value", probe.value }, { "hits", probe.hits }, { "tick", probe.tick } };
		}
		return result;
	}

	if (cmd == "probe_clear") {
		std::lock_guard<std::mutex> lock(_probes_mutex);
		_probes.clear();
		return "ok";
	}

	if (cmd == "obj_types") return DebugBridgeReflectTypes();

	if (cmd == "objs") {
		std::string error;
		json result = DebugBridgeReflectList(Arg(args, 1, "type"), HasFlag(args, 2, "all"), error);
		if (result.is_null()) throw BridgeError{ error };
		return result;
	}

	if (cmd == "obj" || cmd == "obj_schema") {
		const std::string &type = Arg(args, 1, "type");
		size_t id = type == "globals" ? 0 : static_cast<size_t>(ParseInt(Arg(args, 2, "id"), "id"));
		std::vector<std::string> fields;
		for (size_t i = (type == "globals" ? 2 : 3); i < args.size(); i++) fields.push_back(args[i]);
		std::string error;
		json result = DebugBridgeReflectObject(type, id, fields, cmd == "obj_schema", error);
		if (result.is_null()) throw BridgeError{ error };
		return result;
	}

	if (cmd == "screenshot") {
		std::string path = args.size() > 1 ? args[1] : std::string{};
		std::string type = args.size() > 2 ? args[2] : "screen";
		ScreenshotType st;
		if (type == "screen") {
			RequireScreen();
			st = SC_CRASHLOG;
		} else if (type == "viewport") {
			st = SC_VIEWPORT;
		} else if (type == "world") {
			st = SC_WORLD;
		} else if (type == "minimap") {
			st = SC_MINIMAP;
		} else {
			throw BridgeError{ "unknown screenshot type" };
		}
		/* Take the screenshot on the next poll, after the pending window updates have been drawn. */
		conn.pending = [path, st]() -> std::optional<json> {
			if (!MakeScreenshotNowSilent(st, "debug_bridge_screenshot")) throw BridgeError{ "screenshot failed" };
			std::filesystem::path made(_full_screenshot_path);
			if (path.empty()) return json{ { "path", std::filesystem::absolute(made).string() } };
			std::filesystem::path target = std::filesystem::absolute(path);
			std::error_code ec;
			if (target.has_parent_path()) std::filesystem::create_directories(target.parent_path(), ec);
			std::filesystem::rename(made, target, ec);
			if (ec) {
				ec.clear();
				std::filesystem::copy_file(made, target, std::filesystem::copy_options::overwrite_existing, ec);
				if (ec) throw BridgeError{ fmt::format("could not move screenshot to '{}': {}", target.string(), ec.message()) };
				std::filesystem::remove(made, ec);
			}
			return json{ { "path", target.string() } };
		};
		return nullptr;
	}

	if (cmd == "windows") {
		json list = json::array();
		for (const Window *w : Window::IterateFromBack()) {
			if (w->window_class == WindowClass::Invalid) continue;
			list.push_back(DescribeWindow(w));
		}
		return list;
	}

	if (cmd == "widgets") {
		Window *w = FindWindowArg(args, 1);
		json j = DescribeWindow(w);
		j["widgets"] = DescribeWidgets(w);
		return j;
	}

	if (cmd == "close_window") {
		FindWindowArg(args, 1)->Close();
		return "ok";
	}

	if (cmd == "move") {
		RequireScreen();
		MoveMouse(ParseInt(Arg(args, 1, "x"), "x"), ParseInt(Arg(args, 2, "y"), "y"));
		return "ok";
	}

	if (cmd == "click") {
		RequireScreen();
		int x = ParseInt(Arg(args, 1, "x"), "x");
		int y = ParseInt(Arg(args, 2, "y"), "y");
		Click(x, y, HasFlag(args, 3, "right"), HasFlag(args, 3, "ctrl"), HasFlag(args, 3, "shift"));
		return "ok";
	}

	if (cmd == "click_widget") {
		RequireScreen();
		Window *w = FindWindowArg(args, 1);
		WidgetID widget = static_cast<WidgetID>(ParseInt(Arg(args, 3, "widget"), "widget"));
		const NWidgetBase *nwid = w->GetWidget<NWidgetBase>(widget);
		if (nwid == nullptr) throw BridgeError{ "no such widget" };
		if (nwid->current_x == 0 || nwid->current_y == 0) throw BridgeError{ "widget is not visible" };
		int x = w->left + nwid->pos_x + nwid->current_x / 2;
		int y = w->top + nwid->pos_y + nwid->current_y / 2;
		Click(x, y, HasFlag(args, 4, "right"), HasFlag(args, 4, "ctrl"), HasFlag(args, 4, "shift"));
		return { { "x", x }, { "y", y } };
	}

	if (cmd == "drag") {
		RequireScreen();
		int x1 = ParseInt(Arg(args, 1, "x1"), "x1");
		int y1 = ParseInt(Arg(args, 2, "y1"), "y1");
		int x2 = ParseInt(Arg(args, 3, "x2"), "x2");
		int y2 = ParseInt(Arg(args, 4, "y2"), "y2");
		int steps = args.size() > 5 ? std::max<int>(1, ParseInt(args[5], "steps")) : 10;
		MoveMouse(x1, y1);
		_left_button_down = true;
		HandleMouseEvents();
		for (int i = 1; i <= steps; i++) {
			MoveMouse(x1 + (x2 - x1) * i / steps, y1 + (y2 - y1) * i / steps);
		}
		_left_button_down = false;
		_left_button_clicked = false;
		HandleMouseEvents();
		return "ok";
	}

	if (cmd == "wheel") {
		RequireScreen();
		MoveMouse(ParseInt(Arg(args, 1, "x"), "x"), ParseInt(Arg(args, 2, "y"), "y"));
		_cursor.wheel += static_cast<int>(ParseInt(Arg(args, 3, "delta"), "delta"));
		HandleMouseEvents();
		return "ok";
	}

	if (cmd == "key") {
		RequireScreen();
		char32_t unicode;
		uint16_t keycode = ParseKey(Arg(args, 1, "key"), unicode);
		if (HasFlag(args, 2, "ctrl")) keycode |= WKC_CTRL;
		if (HasFlag(args, 2, "shift")) keycode |= WKC_SHIFT;
		if (HasFlag(args, 2, "alt")) keycode |= WKC_ALT;
		ModifierScope mods(HasFlag(args, 2, "ctrl"), HasFlag(args, 2, "shift"));
		HandleKeypress(keycode, unicode);
		return "ok";
	}

	if (cmd == "text") {
		RequireScreen();
		HandleTextInput(Arg(args, 1, "text"));
		return "ok";
	}

	if (cmd == "tile_screen") {
		Point pt = TileToScreen(ParseInt(Arg(args, 1, "tx"), "tx"), ParseInt(Arg(args, 2, "ty"), "ty"));
		bool visible = pt.x >= 0 && pt.y >= 0 && pt.x < _screen.width && pt.y < _screen.height;
		return { { "x", pt.x }, { "y", pt.y }, { "on_screen", visible } };
	}

	if (cmd == "screen_tile") {
		const Window *w = GetMainWindow();
		if (w == nullptr || w->viewport == nullptr) throw BridgeError{ "no main viewport" };
		Point pt = TranslateXYToTileCoord(w->viewport, ParseInt(Arg(args, 1, "x"), "x"), ParseInt(Arg(args, 2, "y"), "y"));
		if (pt.x < 0) return nullptr;
		uint tx = pt.x / TILE_SIZE;
		uint ty = pt.y / TILE_SIZE;
		return { { "tx", tx }, { "ty", ty }, { "tile", TileXY(tx, ty).base() } };
	}

	if (cmd == "resize") {
		RequireScreen();
		int w = ParseInt(Arg(args, 1, "width"), "width");
		int h = ParseInt(Arg(args, 2, "height"), "height");
		if (!VideoDriver::GetInstance()->ChangeResolution(w, h)) throw BridgeError{ "resize not supported by this video driver" };
		return { { "width", _screen.width }, { "height", _screen.height } };
	}

	if (cmd == "lockstep") {
		const std::string &mode = Arg(args, 1, "on|off");
		if (mode == "on") {
			if (!_lockstep_supported) throw BridgeError{ "lockstep is only supported by the headless video driver" };
			if (_networking) throw BridgeError{ "lockstep is not available in network games" };
			SetLockstep(true);
		} else if (mode == "off") {
			SetLockstep(false);
		} else {
			throw BridgeError{ "expected on or off" };
		}
		return { { "lockstep", _lockstep } };
	}

	if (cmd == "step") {
		if (!_lockstep) throw BridgeError{ "not in lockstep mode, use 'lockstep on' first (or wait_ticks in real-time mode)" };
		uint64_t n = ParseInt(Arg(args, 1, "ticks"), "ticks");
		Probe("bridge.last_step", n);
		_lockstep_pending += n;
		uint64_t start = _tick_counter;
		/* Reply once all ticks were run, and the result was drawn (the reply happens in the next draw tick's input loop). */
		conn.pending = [start]() -> std::optional<json> {
			if (_lockstep_pending > 0) return std::nullopt;
			return json{ { "tick_counter", _tick_counter }, { "advanced", _tick_counter - start } };
		};
		return nullptr;
	}

	if (cmd == "wait_ticks") {
		uint64_t n = ParseInt(Arg(args, 1, "ticks"), "ticks");
		auto deadline = Clock::now() + std::chrono::milliseconds(args.size() > 2 ? ParseInt(args[2], "timeout_ms") : 60000);
		uint64_t target = _tick_counter + n;
		if (_lockstep) _lockstep_pending += n;
		conn.pending = [target, deadline]() -> std::optional<json> {
			if (_tick_counter >= target) return json{ { "tick_counter", _tick_counter } };
			if (Clock::now() > deadline) throw BridgeError{ fmt::format("timeout, tick_counter={} target={} (game paused?)", _tick_counter, target) };
			return std::nullopt;
		};
		return nullptr;
	}

	if (cmd == "wait_frames") {
		auto remaining = std::make_shared<int64_t>(ParseInt(Arg(args, 1, "frames"), "frames"));
		conn.pending = [remaining]() -> std::optional<json> {
			if ((*remaining)-- > 0) return std::nullopt;
			return json("ok");
		};
		return nullptr;
	}

	if (cmd == "show") {
		if (args.size() < 2) return DebugBridgeWindowNames();
		std::string error = DebugBridgeShowWindow(args[1], args.size() > 2 ? ParseInt(args[2], "id") : -1);
		if (!error.empty()) throw BridgeError{ error };
		return "ok";
	}

	if (cmd == "engines") return DebugBridgeEngineList(args.size() > 1 ? args[1] : std::string_view{});

	if (cmd == "tiles") {
		if (_game_mode == GameMode::Menu || Map::SizeX() == 0) throw BridgeError{ "no map loaded" };
		uint x = args.size() > 1 ? ParseInt(args[1], "x") : 0;
		uint y = args.size() > 2 ? ParseInt(args[2], "y") : 0;
		uint w = args.size() > 3 ? ParseInt(args[3], "w") : Map::SizeX();
		uint h = args.size() > 4 ? ParseInt(args[4], "h") : Map::SizeY();
		if (x >= Map::SizeX() || y >= Map::SizeY()) throw BridgeError{ "tile outside map" };
		return DebugBridgeTiles(x, y, std::min<uint>(w, Map::SizeX() - x), std::min<uint>(h, Map::SizeY() - y));
	}

	if (cmd == "cmds") return DebugBridgeCommandList(args.size() > 1 ? args[1] : std::string_view{});

	if (cmd == "docmd" || cmd == "docmd_hex" || cmd == "order") {
		auto container = std::make_shared<DynBaseCommandContainer>();
		size_t first = 1;
		const bool test_only = args.size() > first && args[first] == "test";
		if (test_only) first++;
		/* "as <company>": act for another company; only on the server (e.g. server-only commands like MoneyCheatAdmin). */
		CompanyID company = _local_company;
		if (args.size() > first + 1 && args[first] == "as") {
			if (_networking && !_network_server) throw BridgeError{ "'as <company>' is only available on the server" };
			company = static_cast<CompanyID>(ParseInt(args[first + 1], "company"));
			first += 2;
		}
		std::string error;
		bool built;
		if (cmd == "docmd") {
			built = DebugBridgeBuildCommand(args, first, *container, error);
		} else if (cmd == "docmd_hex") {
			built = DebugBridgeBuildRawCommand(args, first, *container, error);
		} else {
			built = DebugBridgeBuildOrder(args, first, *container, error);
		}
		if (!built) throw BridgeError{ error };
		if (test_only) return DebugBridgeTestCommand(*container, company);

		/* Reply once the command was executed (in multiplayer: after the server distributed it back to us).
		 * This also keeps clients within the per-frame command limits. */
		static uint32_t next_seq = 0;
		const uint32_t seq = ++next_seq;
		auto submitted = std::make_shared<bool>(false);
		auto deadline = Clock::now() + std::chrono::seconds(60);
		conn.pending = [container, seq, submitted, deadline, company]() -> std::optional<json> {
			if (!*submitted) {
				if (_networking && _network_client_commands_sent >= 2) {
					if (Clock::now() > deadline) throw BridgeError{ "timeout waiting to send the command" };
					return std::nullopt;
				}
				*submitted = true;
				if (auto result = DebugBridgeSubmitCommand(*container, seq, company)) return result;
				return std::nullopt;
			}
			if (auto result = DebugBridgeTakeCommandResult(seq)) return result;
			if (!_networking) throw BridgeError{ "disconnected before the command was executed" };
			if (Clock::now() > deadline) {
				DebugBridgeForgetCommandResult(seq);
				throw BridgeError{ "timeout waiting for the command to be executed" };
			}
			return std::nullopt;
		};
		return nullptr;
	}

	throw BridgeError{ fmt::format("unknown command '{}', try 'help'", cmd) };
}

/* ---- Networking ---- */

static void Reply(Connection &conn, const json &response)
{
	conn.out += response.dump(-1, ' ', false, json::error_handler_t::replace);
	conn.out += '\n';
}

static void ReplyResult(Connection &conn, json result)
{
	Reply(conn, json{ { "ok", true }, { "result", std::move(result) } });
}

static void ReplyError(Connection &conn, std::string_view error)
{
	Reply(conn, json{ { "ok", false }, { "error", error } });
}

/** Run the pending deferred operation of a connection; returns whether it is (now) idle. */
static bool RunPending(Connection &conn)
{
	if (!conn.pending) return true;
	try {
		std::optional<json> result = conn.pending();
		if (!result.has_value()) return false;
		conn.pending = nullptr;
		ReplyResult(conn, std::move(*result));
	} catch (const BridgeError &e) {
		conn.pending = nullptr;
		ReplyError(conn, e.message);
	} catch (const std::exception &e) {
		conn.pending = nullptr;
		ReplyError(conn, e.what());
	}
	return true;
}

static void ProcessLines(Connection &conn)
{
	for (;;) {
		if (!RunPending(conn)) return;

		size_t eol = conn.in.find('\n');
		if (eol == std::string::npos) return;
		std::string line = conn.in.substr(0, eol);
		conn.in.erase(0, eol + 1);
		if (!line.empty() && line.back() == '\r') line.pop_back();

		std::vector<std::string> args = Tokenise(line);
		if (args.empty()) continue;

		try {
			json result = HandleCommand(conn, args, line);
			/* Deferred commands are first evaluated on the next poll, i.e. after the next window redraw. */
			if (conn.pending) return;
			ReplyResult(conn, std::move(result));
		} catch (const BridgeError &e) {
			ReplyError(conn, e.message);
		} catch (const std::exception &e) {
			ReplyError(conn, e.what());
		}
	}
}

static void ReadFromConnection(Connection &conn)
{
	char buf[4096];
	for (;;) {
		ssize_t n = recv(conn.sock, buf, sizeof(buf), 0);
		if (n > 0) {
			conn.in.append(buf, n);
			continue;
		}
		if (n == 0) {
			conn.closing = true;
		} else if (!NetworkError::GetLast().WouldBlock()) {
			conn.closing = true;
		}
		return;
	}
}

static void WriteToConnection(Connection &conn)
{
	while (!conn.out.empty()) {
		ssize_t n = send(conn.sock, conn.out.data(), static_cast<int>(conn.out.size()), 0);
		if (n > 0) {
			conn.out.erase(0, n);
			continue;
		}
		if (n < 0 && !NetworkError::GetLast().WouldBlock()) {
			conn.out.clear();
			conn.closing = true;
		}
		return;
	}
}

bool Start(uint16_t port)
{
	if (_listen_sock != INVALID_SOCKET) Stop();

	SOCKET sock = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
	if (sock == INVALID_SOCKET) {
		Debug(net, 0, "[debug-bridge] socket() failed: {}", NetworkError::GetLast().AsString());
		return false;
	}

	int on = 1;
	setsockopt(sock, SOL_SOCKET, SO_REUSEADDR, reinterpret_cast<const char *>(&on), sizeof(on));

	sockaddr_in addr{};
	addr.sin_family = AF_INET;
	addr.sin_port = htons(port);
	addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);

	if (bind(sock, reinterpret_cast<sockaddr *>(&addr), sizeof(addr)) != 0 || listen(sock, 4) != 0) {
		Debug(net, 0, "[debug-bridge] could not listen on 127.0.0.1:{}: {}", port, NetworkError::GetLast().AsString());
		closesocket(sock);
		return false;
	}
	SetNonBlocking(sock);

	_listen_sock = sock;
	_port = port;
	_active = true;
	RegisterBuiltinWatches();
	Debug(net, 0, "[debug-bridge] listening on 127.0.0.1:{}", port);
	return true;
}

void Stop()
{
	for (auto &conn : _connections) closesocket(conn->sock);
	_connections.clear();
	if (_listen_sock != INVALID_SOCKET) closesocket(_listen_sock);
	_listen_sock = INVALID_SOCKET;
	_port = 0;
	_active = false;
}

void StartFromEnvironment()
{
	const char *env = getenv("OTTD_DEBUG_BRIDGE");
	if (env == nullptr || *env == '\0') return;
	int port = 0;
	std::string_view str{env};
	auto [ptr, ec] = std::from_chars(str.data(), str.data() + str.size(), port);
	if (ec != std::errc{} || ptr != str.data() + str.size() || port <= 0 || port > 65535) {
		Debug(net, 0, "[debug-bridge] invalid OTTD_DEBUG_BRIDGE port: {}", env);
		return;
	}
	Start(static_cast<uint16_t>(port));
}

uint16_t GetPort()
{
	return _port;
}

void Poll()
{
	if (_listen_sock == INVALID_SOCKET) return;

	for (;;) {
		SOCKET s = accept(_listen_sock, nullptr, nullptr);
		if (s == INVALID_SOCKET) break;
		SetNonBlocking(s);
		SetNoDelay(s);
		auto conn = std::make_unique<Connection>();
		conn->sock = s;
		_connections.push_back(std::move(conn));
	}

	for (auto &conn : _connections) {
		ReadFromConnection(*conn);
		ProcessLines(*conn);
		WriteToConnection(*conn);
	}

	std::erase_if(_connections, [](const std::unique_ptr<Connection> &conn) {
		if (!conn->closing || !conn->out.empty()) return false;
		closesocket(conn->sock);
		return true;
	});
}

} // namespace DebugBridge

DEBUG_BRIDGE_WATCH("bridge.connections", DebugBridge::_connections.size());
